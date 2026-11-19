"""Application hooks for `crm_coldcall`.

This file wires every customization block in a modular, editable way:

   1. Smart PDF Processing      -> api.pdf_import + DocType Cold Call PDF Import
   2. Permissions & Recording   -> api.permissions + api.call_recording
   3. Service Table Formatting  -> DocType Cold Call Service Item (child table)
   4. Lead Status Segmentation  -> api.lead_pipeline (4 modes)
   5. Agent RBAC                 -> api.agent_access + api.audit_log +
                                    DocTypes Agent Module Access / Cold Call Audit Log


Each section below is marked so future developers can edit one block
without touching the others.
"""

app_name = "crm_coldcall"
app_title = "Cold Call Custom"
app_publisher = "Software Solutions"
app_description = "Cold-call customizations for Frappe CRM: PDF import, recording guard, service table, 4-mode pipeline"
app_email = "admin@example.com"
app_license = "mit"

# ---------------------------------------------------------------------------
# BLOCK 1: Required apps (Frappe CRM stack).
# ---------------------------------------------------------------------------
# Frappe Framework is implicit. CRM app must be present because we extend
# its DocTypes (CRM Lead, CRM Call Log, CRM Lead Status).
required_apps = ["crm"]

# ---------------------------------------------------------------------------
# BLOCK 2: Installation / migration entry points.
# ---------------------------------------------------------------------------
# `after_install` seeds: 4 lead statuses, custom fields on CRM Lead,
# custom fields on CRM Call Log (disposition/auto-log), permlevel lockdown
# on recording_url, Cold Call Agent role, client scripts.
# `after_migrate` re-runs the same seeder idempotently so rebuilds stay safe.
after_install = "crm_coldcall.install.after_install"
after_migrate = "crm_coldcall.install.after_migrate"

# ---------------------------------------------------------------------------
# BLOCK 3: Document lifecycle hooks (server-side automation).
# ---------------------------------------------------------------------------
doc_events = {
    # --- Call logging + recording automation (features 2 + 3) -----------
    "CRM Call Log": {
        # before: auto-create log details (id/type/status/medium/start/
        # caller/from-to) THEN stamp the private recording path for EVERY
        # call (Incoming + Outgoing) — no manual agent activation.
        # RBAC guard runs second: Agents may create call logs (View/Create/
        # Edit, no Delete); every other module stays create-blocked.
        # Admin/telecom inserts are unaffected.
        "before_insert": [
            "crm_coldcall.api.call_recording.automate_call_log",
            "crm_coldcall.api.agent_access.guard_create",
        ],
        # Re-validate on update (e.g. Twilio/Exotel webhook filled the URL).
        "before_save": "crm_coldcall.api.call_recording.automate_call_log",
        # After: push the officer's disposition onto the linked Lead
        # (status / custom_call_outcome) + stamp end_time/duration.
        "after_insert": [
            "crm_coldcall.api.call_recording.sync_disposition_to_lead",
            "crm_coldcall.api.audit_log.audit_write",
        ],
        "on_update": [
            "crm_coldcall.api.call_recording.sync_disposition_to_lead",
            "crm_coldcall.api.audit_log.audit_write",
        ],
    },
    # --- Lead pipeline guard --------------------------------------------
    "CRM Lead": {
        # RBAC first: hard-block Agent creates (backend 403), then
        # normalise legacy statuses into the 4 approved modes.
        "before_insert": "crm_coldcall.api.agent_access.guard_create",
        "before_save": "crm_coldcall.api.lead_pipeline.normalise_lead_status",
        "after_insert": "crm_coldcall.api.audit_log.audit_write",
        "on_update": "crm_coldcall.api.audit_log.audit_write",
    },
    # --- Deal / Task / Contact / Organization create-guard + audit (RBAC)
    "CRM Deal": {
        # guard_create 403s direct agent creates; conversion inserts bypass
        # it via ignore_permissions. strip_conversion_owner then keeps
        # agent conversions person-less (deal_owner stays empty).
        "before_insert": [
            "crm_coldcall.api.agent_access.guard_create",
            "crm_coldcall.api.agent_access.strip_conversion_owner",
        ],
        "after_insert": "crm_coldcall.api.audit_log.audit_write",
        "on_update": "crm_coldcall.api.audit_log.audit_write",
    },
    "CRM Task": {
        "before_insert": "crm_coldcall.api.agent_access.guard_create",
        "after_insert": "crm_coldcall.api.audit_log.audit_write",
        "on_update": "crm_coldcall.api.audit_log.audit_write",
    },
    "CRM Contacts": {
        "before_insert": "crm_coldcall.api.agent_access.guard_create",
        "after_insert": "crm_coldcall.api.audit_log.audit_write",
        "on_update": "crm_coldcall.api.audit_log.audit_write",
    },
    "Contact": {
        "before_insert": "crm_coldcall.api.agent_access.guard_create",
        "after_insert": "crm_coldcall.api.audit_log.audit_write",
        "on_update": "crm_coldcall.api.audit_log.audit_write",
    },
    "CRM Organization": {
        "before_insert": "crm_coldcall.api.agent_access.guard_create",
        "after_insert": "crm_coldcall.api.audit_log.audit_write",
        "on_update": "crm_coldcall.api.audit_log.audit_write",
    },
}

# ---------------------------------------------------------------------------
# BLOCK 4: Permission hooks (visibility + RBAC matrix enforcement).
# ---------------------------------------------------------------------------
# `has_permission` runs on every DocType access check. We use it to:
#   * hide CRM Call Log.recording_url content from non-admins (field-level
#     permlevel is set in install.py; this is the second, defence-in-depth
#     layer that blocks direct API reads),
#   * block CREATE of all agent modules for Agents (Convert to Deal still
#     works: it inserts with ignore_permissions),
#   * block DELETE of all guarded DocTypes for Agents,
#   * gate READ/WRITE by the Admin-configured Agent Module Access row,
#   * enforce assigned-records-only scope (see query conditions below).
# Every denial is written to Cold Call Audit Log.
has_permission = {
    "CRM Call Log": "crm_coldcall.api.permissions.call_log_has_permission",
    "CRM Lead": "crm_coldcall.api.permissions.lead_has_permission",
    "CRM Task": "crm_coldcall.api.permissions.task_has_permission",
    "CRM Deal": "crm_coldcall.api.permissions.deal_has_permission",
    "CRM Contacts": "crm_coldcall.api.permissions.contact_has_permission",
    "Contact": "crm_coldcall.api.permissions.contact_has_permission",
    "CRM Organization": "crm_coldcall.api.permissions.organization_has_permission",
    "FCRM Note": "crm_coldcall.api.permissions.note_has_permission",
}

# Row-level filters: Agents see only assigned records unless the Admin
# granted view-all for that module. Admins see everything ("" = no filter).
permission_query_conditions = {
    "CRM Call Log": "crm_coldcall.api.permissions.call_log_query_conditions",
    "CRM Lead": "crm_coldcall.api.permissions.lead_query_conditions",
    "CRM Task": "crm_coldcall.api.permissions.task_query_conditions",
    "CRM Deal": "crm_coldcall.api.permissions.deal_query_conditions",
    "CRM Contacts": "crm_coldcall.api.permissions.contacts_query_conditions",
    "Contact": "crm_coldcall.api.permissions.contact_query_conditions",
    "CRM Organization": "crm_coldcall.api.permissions.organization_query_conditions",
}

# ---------------------------------------------------------------------------
# BLOCK 4b: Whitelisted-method overrides.
# ---------------------------------------------------------------------------
# Redirect the stock Lead -> Deal conversion through our wrapper so an
# Agent's conversion ends person-less: deal_owner is cleared by the
# CRM Deal before_insert hook, and carry-over ToDo/DocShare rows the
# stock flow creates for other users are dropped. Admin conversions
# pass straight through (stock behaviour unchanged). Covers the modal,
# bulk "Convert" action and any API caller (handler resolves the
# override before whitelisting).
override_whitelisted_methods = {
    "crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal": (
        "crm_coldcall.api.lead_convert.convert_to_deal"
    ),
}

# ---------------------------------------------------------------------------
# BLOCK 5: Desk UI injections (list/form helpers, no core-file hacks).
# ---------------------------------------------------------------------------
# Loaded on every Desk page. The JS itself checks `frappe.user.has_role`
# before hiding recording fields or injecting the 4 pipeline tabs, so Admins
# are unaffected.
app_include_js = "/assets/crm_coldcall/js/lead_pipeline_tabs.js"

# ---------------------------------------------------------------------------
# BLOCK 6: Scheduler (optional follow-up nudges).
# ---------------------------------------------------------------------------
scheduler_events = {
    # Every morning, flag Follow-up leads whose next touchpoint is overdue.
    # Purely informational; safe to disable by commenting out.
    "daily": [
        "crm_coldcall.api.lead_pipeline.flag_overdue_followups",
    ],
}
