"""Lead Status Segmentation — 4 modes (scope §4).

Modes (colour-coded per the approved scope):
  🟢 Interested     — active hot leads (CRM Lead Status type Ongoing)
  🔴 Not Interested — archiving target (type Lost, kept for reference)
  🟡 Follow-up      — time-sensitive touchpoints (needs custom_next_followup)
  🔵 No Answer      — pending / no-answer path (type On Hold)

The wireflow maps cleanly:
  Cold Call -> No Answer   => status "No Answer"  + follow-up task
            -> Not Interested => status "Not Interested" (Mark Lost)
            -> Interested  => status "Interested" -> Requirement -> Meeting

Legacy data ("New", "Contacted", ...) is normalised on save by
`normalise_lead_status` so old rows flow into the 4 tabs without migration.
"""

import frappe
from frappe import _

# ---------------------------------------------------------------------------
# TUNABLES — canonical mode names (must exist in CRM Lead Status).
# ---------------------------------------------------------------------------
MODES = ["Interested", "Not Interested", "Follow-up", "No Answer"]

MODE_META = {
    "Interested": {"emoji": "🟢", "desc": "Active hot leads."},
    "Not Interested": {"emoji": "🔴", "desc": "Archiving target paths."},
    "Follow-up": {"emoji": "🟡", "desc": "Time-sensitive scheduled touchpoints."},
    "No Answer": {"emoji": "🔵", "desc": "No answer / pending — retry scheduled."},
}

# Legacy -> canonical mapping for old databases / imports.
LEGACY_MAP = {
    "New": "No Answer",
    "Contacted": "Follow-up",
    "Nurture": "Follow-up",
    "Qualified": "Interested",
    "Unqualified": "Not Interested",
    "Lost": "Not Interested",
    "Converted": "Interested",
}


# ---------------------------------------------------------------------------
# DOC HOOK — normalise on every Lead save.
# ---------------------------------------------------------------------------

def normalise_lead_status(doc, method=None):
    """Rewrite legacy statuses to the 4 canonical modes (before_save).

    Also keeps `custom_call_outcome` in sync with `status` so the dashboard
    tabs and the outcome dropdown never disagree:
      status Interested     -> outcome Interested
      status Not Interested -> outcome Not Interested
      status Follow-up      -> outcome Follow-up
      status No Answer      -> outcome No Answer
    """
    if doc.get("status") in LEGACY_MAP:
        doc.status = LEGACY_MAP[doc.status]
    # Outcome mirror (only when outcome is empty or stale-legacy).
    mirror = {
        "Interested": "Interested",
        "Not Interested": "Not Interested",
        "Follow-up": "Follow-up",
        "No Answer": "No Answer",
    }
    if doc.get("status") in mirror:
        # Do not clobber an explicit "Meeting Scheduled" outcome.
        if not doc.get("custom_call_outcome") or doc.custom_call_outcome in LEGACY_MAP:
            doc.custom_call_outcome = mirror[doc.status]


# ---------------------------------------------------------------------------
# DASHBOARD APIs — whitelisted for the Vue/Desk tab bar.
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_pipeline_counts():
    """Return {mode: count} for the 4 dashboard tabs (respects permissions)."""
    counts = {}
    for mode in MODES:
        counts[mode] = frappe.db.count("CRM Lead", {"status": mode})
    counts["Total"] = sum(counts.values())
    return {"counts": counts, "meta": MODE_META}


@frappe.whitelist()
def get_leads_by_mode(mode: str, limit: int = 50):
    """Return Lead rows for one pipeline tab (for custom dashboards).

    Args:
        mode: one of the 4 MODES (validated).
        limit: max rows (capped at 200 for performance).
    """
    if mode not in MODES:
        frappe.throw(_("Unknown pipeline mode: {0}").format(mode))
    limit = min(int(limit or 50), 200)
    from crm_coldcall.api.permissions import redact_recording_url  # noqa (kept for symmetry)
    rows = frappe.get_list(
        "CRM Lead",
        filters={"status": mode},
        fields=["name", "lead_name", "organization", "mobile_no", "email",
                "status", "custom_call_outcome", "custom_next_followup",
                "lead_owner", "modified"],
        order_by="modified desc",
        limit_page_length=limit,
    )
    return {"mode": mode, "meta": MODE_META[mode], "leads": rows}


# ---------------------------------------------------------------------------
# SCHEDULER — daily overdue follow-up flag.
# ---------------------------------------------------------------------------

def flag_overdue_followups():
    """Log (not email-spam) Follow-up / No Answer leads past their date.

    Runs daily via hooks.scheduler_events. Creates one FCRM Note per overdue
    lead the first time it is seen — safe and idempotent.
    """
    from frappe.utils import today
    overdue = frappe.get_all(
        "CRM Lead",
        filters={
            "status": ["in", ["Follow-up", "No Answer"]],
            "custom_next_followup": ["<", today()],
        },
        fields=["name", "lead_name", "custom_next_followup"],
        limit=200,
    )
    for lead in overdue:
        # Skip if we already flagged this lead for this date.
        marker = f"Overdue follow-up flagged for {lead.custom_next_followup}"
        if frappe.db.exists("FCRM Note", {"note": marker, "reference_docname": lead.name}):
            continue
        note = frappe.new_doc("FCRM Note")
        note.note = marker
        note.reference_doctype = "CRM Lead"
        note.reference_docname = lead.name
        note.insert(ignore_permissions=True)
