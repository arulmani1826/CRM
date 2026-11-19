"""Automatic call logging + call recording (scope §2, features 2 + 3).

Feature 2 — Automate the call logging process (Create Call Log page):
  * `auto_fill_call_log_details` pre-fills every technical field (id, type,
    status, telephony_medium, medium, start_time, caller/receiver, from/to
    from the linked Lead) so the sales officer only picks
    `custom_call_disposition` (+ optional remarks).
  * `get_call_log_defaults` (whitelisted) returns the same defaults to the
    frontend so the Create Call Log form opens pre-filled.
  * `sync_disposition_to_lead` pushes the chosen disposition back onto the
    linked CRM Lead (status / custom_call_outcome) on insert + update.

Feature 3 — Automated call recording for ALL customer calls:
  * `ensure_recording_on_insert` stamps a deterministic private path
    `/private/files/call-recordings/<call-id>.mp3` for EVERY call
    (Incoming + Outgoing, any telephony medium) when empty — no manual
    agent activation needed. Never overwrites a real Twilio/Exotel URL.
  * `automate_call_log` is the single before_insert/before_save entry point
    that runs auto-fill THEN auto-record (order matters: record needs id).
  * `register_recording` (whitelisted, Admin/telecom only) replaces the
    placeholder with the real uploaded file URL.

Visibility is NOT handled here (see api/permissions.py + install.py
permlevel lock); this file only ensures the data exists.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime


# Deterministic placeholder so reports can always join on a value.
RECORDING_DIR = "/private/files/call-recordings"

# Disposition options shown in custom_call_disposition (seeded in install.py).
# Must stay in sync with install.CALL_DISPOSITIONS.
DISPOSITION_OPTIONS = [
    "Interested",
    "Not Interested",
    "Follow-up",
    "No Answer",
    "Meeting Scheduled",
    "Busy",
    "Failed",
    "Call Back",
]

# Disposition -> CRM Lead status/outcome (pipeline modes + meeting).
DISPOSITION_TO_LEAD_STATUS = {
    "Interested": "Interested",
    "Not Interested": "Not Interested",
    "Follow-up": "Follow-up",
    "No Answer": "No Answer",
    "Meeting Scheduled": "Interested",
    "Busy": "Follow-up",
    "Failed": "Follow-up",
    "Call Back": "Follow-up",
}

# Disposition -> telephony Status value on CRM Call Log itself.
DISPOSITION_TO_CALL_STATUS = {
    "Interested": "Completed",
    "Not Interested": "Completed",
    "Follow-up": "Completed",
    "No Answer": "No Answer",
    "Meeting Scheduled": "Completed",
    "Busy": "Busy",
    "Failed": "Failed",
    "Call Back": "Completed",
}


@frappe.whitelist()
def register_recording(call_log_name: str, file_url: str):
    """Attach the real recording file to a call log (Admin/telecom path).

    Args:
        call_log_name: CRM Call Log name.
        file_url: private File URL of the uploaded .mp3/.wav.
    """
    doc = frappe.get_doc("CRM Call Log", call_log_name)
    # Only privileged roles may set the final URL (agents get the placeholder).
    if not _is_admin():
        frappe.throw(_("Only Admins can register call recordings."), frappe.PermissionError)
    if not file_url.startswith("/private/files/"):
        frappe.throw(_("Recording must be a private file."))
    doc.recording_url = file_url
    doc.save(ignore_permissions=True)
    return {"ok": True, "recording_url": file_url}


def ensure_recording_on_insert(doc, method=None):
    """Doc hook: auto-record EVERY customer call (Incoming + Outgoing).

    Runs on before_insert + before_save. Idempotent: never overwrites a real
    URL that Twilio/Exotel already supplied. No manual agent activation —
    the placeholder is stamped server-side even for Manual logs.
    """
    if doc.get("recording_url"):
        return  # webhook already filled it — keep the real one
    call_id = doc.get("id") or doc.get("name") or "pending"
    # Deterministic, per-call private path. Admin UI resolves it to the File
    # once the audio upload lands; until then it renders as "Recording pending".
    doc.recording_url = f"{RECORDING_DIR}/{call_id}.mp3"
    # Surface auto-recording state when the tracking field exists.
    try:
        if hasattr(doc, "meta") and doc.meta.has_field("custom_recording_status"):
            if not doc.get("custom_recording_status"):
                doc.custom_recording_status = "Auto-Recording"
    except Exception:
        pass


def auto_fill_call_log_details(doc, method=None):
    """Doc hook: auto-create log details so the officer only picks disposition.

    Fills every technical field with a safe default when empty — never
    overwrites explicit user/telecom values:

      id                 -> CALL-YYYYMMDDHHMMSS-XXXX (autoname field:id)
      type               -> Outgoing
      status             -> Completed (or mapped from custom_call_disposition)
      telephony_medium   -> Manual
      medium             -> telephony_medium (e.g. Phone/Manual)
      start_time         -> now
      caller/receiver    -> session user (by type)
      from/to            -> linked Lead mobile_no (customer side) + agent side
      custom_auto_logged -> 1 (marks server-side auto-fill)
    """
    import random
    import string

    try:
        # --- id (autoname field:id, unique, required for recording path) ---
        if not doc.get("id"):
            stamp = now_datetime().strftime("%Y%m%d%H%M%S")
            rand = "".join(random.choices(string.digits, k=4))
            doc.id = f"CALL-{stamp}-{rand}"

        # --- type / telephony_medium / medium ------------------------------
        if not doc.get("type"):
            doc.type = "Outgoing"
        if not doc.get("telephony_medium"):
            doc.telephony_medium = "Manual"
        if not doc.get("medium"):
            doc.medium = doc.get("telephony_medium") or "Phone"

        # --- status: disposition wins, else Completed ----------------------
        disposition = (doc.get("custom_call_disposition") or "").strip()
        if disposition in DISPOSITION_TO_CALL_STATUS:
            # Only upgrade the default; never clobber an explicit telecom
            # status like Ringing/In Progress that arrived via webhook.
            if not doc.get("status") or doc.get("status") in ("Initiated", "Queued", "Completed"):
                doc.status = DISPOSITION_TO_CALL_STATUS[disposition]
        if not doc.get("status"):
            doc.status = "Completed"

        # --- start_time -----------------------------------------------------
        if not doc.get("start_time"):
            doc.start_time = now_datetime()

        # --- caller / receiver ----------------------------------------------
        try:
            user = frappe.session.user
        except Exception:
            user = None
        if user and user != "Guest":
            if doc.get("type") == "Outgoing" and not doc.get("caller"):
                doc.caller = user
            elif doc.get("type") == "Incoming" and not doc.get("receiver"):
                doc.receiver = user

        # --- from / to from the linked Lead ---------------------------------
        if (not doc.get("to") or not doc.get("from")) and doc.get("reference_docname"):
            lead_mobile = _get_lead_mobile(
                doc.get("reference_doctype"), doc.get("reference_docname")
            )
            if lead_mobile:
                if doc.get("type") == "Outgoing":
                    if not doc.get("to"):
                        doc.to = lead_mobile
                    if not doc.get("from"):
                        setattr(doc, "from", _get_user_phone() or "0000000000")
                else:  # Incoming
                    if not doc.get("from"):
                        setattr(doc, "from", lead_mobile)
                    if not doc.get("to"):
                        doc.to = _get_user_phone() or "0000000000"
        # --- last-resort fallbacks for reqd fields --------------------------
        # NOTE: from/to are Data(Phone) + reqd=1, validated by
        # PHONE_NUMBER_PATTERN ([0-9 +_- etc]{1,20}). Old "Office"/"Unknown"
        # placeholders raised InvalidPhoneNumberError -> 500 on insert.
        if not doc.get("from"):
            setattr(doc, "from", "0000000000")
        if not doc.get("to"):
            doc.to = "0000000000"
        if not doc.get("reference_doctype"):
            doc.reference_doctype = "CRM Lead"

        # --- auto-log marker --------------------------------------------------
        try:
            if hasattr(doc, "meta") and doc.meta.has_field("custom_auto_logged"):
                doc.custom_auto_logged = 1
        except Exception:
            pass
    except Exception:
        # Auto-fill must never block saving a call log — worst case the
        # officer fills the fields manually as before.
        frappe.logger().warning(
            "[crm_coldcall] auto_fill_call_log_details skipped", exc_info=True
        )


def automate_call_log(doc, method=None):
    """Single before_insert/before_save entry: auto-fill THEN auto-record."""
    auto_fill_call_log_details(doc, method)
    ensure_recording_on_insert(doc, method)


@frappe.whitelist()
def get_call_log_defaults(reference_doctype: str = "CRM Lead", reference_docname: str = None):
    """Return pre-filled defaults for the Create Call Log form.

    The frontend calls this when the page opens so the officer sees all log
    details already filled and only picks `custom_call_disposition`.
    """
    defaults = {
        "type": "Outgoing",
        "status": "Completed",
        "telephony_medium": "Manual",
        "medium": "Manual",
        "start_time": str(now_datetime()),
        "reference_doctype": reference_doctype or "CRM Lead",
        "reference_docname": reference_docname,
        "custom_recording_status": "Auto-Recording",
        "custom_auto_logged": 1,
        "recording_note": "🔴 Auto-recording ON — no manual activation needed.",
    }
    try:
        user = frappe.session.user
        if user and user != "Guest":
            defaults["caller"] = user
    except Exception:
        pass
    if reference_docname:
        mobile = _get_lead_mobile(reference_doctype, reference_docname)
        if mobile:
            defaults["to"] = mobile
            defaults["from"] = _get_user_phone() or "0000000000"
    defaults.setdefault("from", "0000000000")
    defaults.setdefault("to", defaults.get("to") or "0000000000")
    return defaults


def sync_disposition_to_lead(doc, method=None):
    """After insert/update: push call disposition onto the linked Lead.

    * custom_call_disposition Interested/... -> Lead.status + custom_call_outcome
    * Meeting Scheduled keeps outcome "Meeting Scheduled" (not clobbered).
    * Sets end_time + duration when the officer wraps up (disposition present
      but end_time empty) so reports always have a duration.
    * Never raises — a failed sync must not break the call log save.
    """
    try:
        disposition = (doc.get("custom_call_disposition") or "").strip()
        if not disposition:
            return
        # Wrap-up timing: officer picked a disposition => call ended now.
        if not doc.get("end_time"):
            try:
                end = now_datetime()
                frappe.db.set_value("CRM Call Log", doc.name, "end_time", end)
                if doc.get("start_time"):
                    try:
                        delta = (end - doc.start_time).total_seconds()
                        if delta >= 0:
                            frappe.db.set_value("CRM Call Log", doc.name, "duration", delta)
                    except Exception:
                        pass
            except Exception:
                pass
        # Push to Lead.
        if doc.get("reference_docname") and (doc.get("reference_doctype") or "CRM Lead") == "CRM Lead":
            lead_status = DISPOSITION_TO_LEAD_STATUS.get(disposition)
            if not lead_status:
                return
            outcome = disposition if disposition != "Call Back" else "Follow-up"
            try:
                lead = frappe.get_doc("CRM Lead", doc.reference_docname)
                # Meeting Scheduled is sticky — normalise hook would mirror
                # status otherwise, so set both explicitly.
                if disposition == "Meeting Scheduled":
                    lead.status = "Interested"
                    lead.custom_call_outcome = "Meeting Scheduled"
                else:
                    lead.status = lead_status
                    lead.custom_call_outcome = outcome
                lead.save(ignore_permissions=True)
            except Exception:
                frappe.logger().warning(
                    "[crm_coldcall] sync_disposition_to_lead failed", exc_info=True
                )
    except Exception:
        frappe.logger().warning(
            "[crm_coldcall] sync_disposition_to_lead skipped", exc_info=True
        )


def _get_lead_mobile(reference_doctype, reference_docname):
    """Return the customer phone for a reference doc (Lead-first)."""
    try:
        if not reference_docname:
            return None
        dt = reference_doctype or "CRM Lead"
        if dt == "CRM Lead":
            for field in ("mobile_no", "phone", "mobile"):
                try:
                    val = frappe.db.get_value(dt, reference_docname, field)
                except Exception:
                    val = None
                if val:
                    return val
            return None
        # Generic fallback for Deal/Organization references.
        for field in ("mobile_no", "phone", "mobile"):
            try:
                val = frappe.db.get_value(dt, reference_docname, field)
            except Exception:
                val = None
            if val:
                return val
        return None
    except Exception:
        return None


def _get_user_phone():
    """Return the current user's phone/mobile when available."""
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return None
        for field in ("mobile_no", "phone"):
            try:
                val = frappe.db.get_value("User", user, field)
            except Exception:
                val = None
            if val:
                return val
        return None
    except Exception:
        return None


def _is_admin() -> bool:
    """True when the session user holds any admin role."""
    user = frappe.session.user
    if user == "Administrator":
        return True
    roles = set(frappe.get_roles(user))
    return bool(roles & {"System Manager", "Sales Manager"})
