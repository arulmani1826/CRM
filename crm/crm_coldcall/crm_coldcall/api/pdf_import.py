"""Smart PDF Processing (scope §1).

Pipeline:
  Agent uploads PDF (private File) -> `parse_and_map` whitelisted endpoint
  -> server validates -> extracts text with pypdf -> regex-maps to Lead
  fields -> writes back to CRM Lead + audit DocType `Cold Call PDF Import`.

Security notes:
  * Only `.pdf`, max 10 MB, must live under `/private/files/` so no public URL.
  * Caller needs write permission on the target CRM Lead; otherwise 403.
  * Never executes embedded JS — pypdf text extraction only.
"""

import io
import json
import re

import frappe
from frappe import _
from pypdf import PdfReader

# ---------------------------------------------------------------------------
# TUNABLES — edit here to support new document layouts.
# ---------------------------------------------------------------------------
MAX_PDF_MB = 10
PRIVATE_PREFIXES = ("/private/files/", "private/files/")

# Regex library: first match wins per key. Ordered from most to least strict.
PATTERNS = {
    "company": [
        r"(?:Company|Organization|Client)\s*[:\-]\s*(.+)",
        r"^([A-Z][\w&., ]+(?:Technologies|Solutions|Ltd|Pvt|Soft|Systems|Labs))",
    ],
    "contact": [r"(?:Contact(?: Person)?|Name)\s*[:\-]\s*([A-Za-z ]{2,60})"],
    "phone": [r"(\+?\d[\d\s\-]{7,15}\d)"],
    "email": [r"([\w.\-+]+@[\w\-]+(?:\.[\w\-]+)+)"],
    "budget": [
        r"(?:Budget|Amount|₹|Rs\.?|INR)\s*[:\-]?\s*([\d,]+(?:\.\d+)?)",
        r"\$\s*([\d,]+(?:\.\d+)?)",
    ],
    "timeline": [r"(?:Timeline|Duration|Delivery)\s*[:\-]\s*(.+)"],
    "requirement": [r"(?:Requirement|Scope|Need|Description)\s*[:\-]\s*(.+)"],
}

# Keyword -> canonical service name (must match SERVICE_OPTIONS in install.py).
SERVICE_KEYWORDS = {
    "website": "Website Development",
    "e-commerce": "E-commerce Website",
    "ecommerce": "E-commerce Website",
    "payment": "Payment Gateway",
    "gateway": "Payment Gateway",
    "dashboard": "Admin Dashboard",
    "admin": "Admin Dashboard",
    "mobile": "Mobile App Development",
    "app": "Mobile App Development",
    "marketing": "Digital Marketing",
    "seo": "Digital Marketing",
    "support": "Maintenance & Support",
    "maintenance": "Maintenance & Support",
    "software": "Custom Software",
}


# ---------------------------------------------------------------------------
# PUBLIC ENDPOINT — called from the Lead form "Parse PDF" button.
# ---------------------------------------------------------------------------

@frappe.whitelist()
def parse_and_map(lead_name: str, file_url: str):
    """Parse an uploaded PDF and map extracted data onto a CRM Lead.

    Args:
        lead_name: CRM Lead name (e.g. CRM-LEAD-2026-0001).
        file_url:  File.file_url of an already-uploaded private PDF.

    Returns:
        dict with `mapped` (field -> value applied) and `raw_excerpt`.
    """
    # --- 1. Permission gate -------------------------------------------------
    if not frappe.has_permission("CRM Lead", "write", lead_name):
        frappe.throw(_("Not permitted to update this Lead."), frappe.PermissionError)

    # --- 2. File validation (secure upload component) -----------------------
    if not file_url or not file_url.lower().endswith(".pdf"):
        frappe.throw(_("Please attach a valid .pdf file first."))
    if not file_url.startswith(PRIVATE_PREFIXES):
        frappe.throw(
            _("PDF must be a private file (starts with /private/files/). "
              "Re-upload with 'Private' checked.")
        )
    file_doc = frappe.get_all(
        "File", filters={"file_url": file_url}, fields=["name", "file_size"], limit=1
    )
    if not file_doc:
        frappe.throw(_("Attached file record not found. Re-upload the PDF."))
    size = file_doc[0].get("file_size") or 0
    if size > MAX_PDF_MB * 1024 * 1024:
        frappe.throw(_("PDF exceeds the {0} MB limit.").format(MAX_PDF_MB))

    # --- 3. Text extraction -------------------------------------------------
    full_path = _resolve_private_path(file_url)
    text = _extract_text(full_path)
    if not text.strip():
        frappe.throw(_("No readable text found in this PDF (scanned image?)."))

    # --- 4. Mapping ----------------------------------------------------------
    mapped = _map_text_to_lead_fields(text)

    # --- 5. Apply to the Lead (dashboard variables) --------------------------
    lead = frappe.get_doc("CRM Lead", lead_name)
    applied = {}
    field_map = {
        "company": "organization",
        "contact": "lead_name",
        "phone": "mobile_no",
        "email": "email",
        "budget": "custom_budget",
        "timeline": "custom_timeline",
        "requirement": "custom_requirement",
    }
    for key, value in mapped.items():
        if key in field_map and value and not lead.get(field_map[key]):
            # Never overwrite data the agent already typed — fill blanks only.
            lead.set(field_map[key], value)
            applied[field_map[key]] = value

    # Detected services append to the child table without duplicating.
    for svc in mapped.get("services", []):
        existing = {r.service_name for r in (lead.get("custom_services") or [])}
        if svc not in existing:
            lead.append("custom_services", {"service_name": svc, "description": ""})

    lead.custom_pdf_summary = _summarise(mapped)
    lead.save(ignore_permissions=False)

    # --- 6. Audit trail -------------------------------------------------------
    log = frappe.new_doc("Cold Call PDF Import")
    log.lead = lead_name
    log.pdf_file = file_url
    log.status = "Mapped"
    log.extracted_json = json.dumps(mapped, indent=2, ensure_ascii=False)[:8000]
    log.insert(ignore_permissions=True)

    return {"mapped": applied, "services": mapped.get("services", []),
            "summary": lead.custom_pdf_summary}


# ---------------------------------------------------------------------------
# INTERNAL HELPERS (unit-testable in isolation)
# ---------------------------------------------------------------------------

def _resolve_private_path(file_url: str) -> str:
    """Convert a /private/files/... URL to an absolute disk path."""
    site_path = frappe.get_site_path()
    rel = file_url.lstrip("/")
    if rel.startswith("private/"):
        rel = rel[len("private/"):]
    else:  # tolerate "files/..." (public) — caller already enforced private
        rel = rel.replace("files/", "", 1)
    import os
    return os.path.join(site_path, "private", "files", rel.split("files/", 1)[-1])


def _extract_text(abs_path: str) -> str:
    """Extract concatenated page text with pypdf (no JS execution)."""
    reader = PdfReader(abs_path)
    pages = []
    for page in reader.pages[:25]:  # cap: first 25 pages are enough for lead data
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(pages)


def _first_match(patterns, text, flags=re.IGNORECASE | re.MULTILINE):
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m.group(1).strip()[:140]
    return ""


def _map_text_to_lead_fields(text: str) -> dict:
    """Apply PATTERNS + SERVICE_KEYWORDS to raw text. Pure function."""
    out = {}
    for key, pats in PATTERNS.items():
        out[key] = _first_match(pats, text)
    # Budget normalisation: strip commas so Currency field accepts it.
    if out.get("budget"):
        out["budget"] = out["budget"].replace(",", "").strip()
    # Service detection: every keyword hit becomes a canonical service row.
    lowered = text.lower()
    found = []
    for kw, canonical in SERVICE_KEYWORDS.items():
        if kw in lowered and canonical not in found:
            found.append(canonical)
    out["services"] = found
    return out


def _summarise(mapped: dict) -> str:
    """One-line human summary stored on custom_pdf_summary."""
    bits = []
    if mapped.get("company"):
        bits.append(mapped["company"])
    if mapped.get("contact"):
        bits.append(f"c/o {mapped['contact']}")
    if mapped.get("phone"):
        bits.append(mapped["phone"])
    if mapped.get("services"):
        bits.append("Services: " + ", ".join(mapped["services"]))
    return " | ".join(bits)[:500]
