"""Idempotent seeder for the Cold-Call CRM customizations.

Run automatically on `bench install-app` (after_install) and on every
`bench migrate` (after_migrate). Safe to re-run: every helper checks for
existence before creating.

Sections:
  A. Roles            - Cold Call Agent (restricted), reuses System/Sales Manager as Admin.
  B. Lead statuses    - the 4 approved pipeline modes + removal of 6 retired ones.
  C. Custom fields    - service child-table + PDF + requirement/meeting fields on CRM Lead.
  C2. Call-log fields - disposition + auto-log markers on CRM Call Log (feat 2/3).
  D. Recording lock   - recording_url -> permlevel 1 (Admin-only).
  E. Client scripts   - hide recording field from Agents, inject 4 tabs.
  F. Property setters - list-view niceties (status in list, etc).
  G. Agent access     - default Agent Module Access rows for existing agents.
  H. Export block     - strip Export rights for agent roles (Import already admin-only).
  I. Assignment UI    - hide CRM Lead.lead_owner (no Assign Agent anywhere).
  J. Agent UI access  - read FCRM Settings so the SPA user menu (Log out) renders.
"""

import frappe


# ---------------------------------------------------------------------------
# CONSTANTS — edit these to change the pipeline without touching logic.
# ---------------------------------------------------------------------------

# 4 approved lead modes: (status name, colour, type, position).
# Colours follow Frappe's allowed palette. Types drive Won/Lost math.
LEAD_STATUSES = [
    # 🟢 Active hot leads — sales works these first.
    {"lead_status": "Interested", "color": "green", "type": "Ongoing", "position": 10},
    # 🔴 Archiving target — stays in DB for future reference, excluded from funnel.
    {"lead_status": "Not Interested", "color": "red", "type": "Lost", "position": 20},
    # 🟡 Time-sensitive touchpoints — needs next_followup date.
    {"lead_status": "Follow-up", "color": "yellow", "type": "Ongoing", "position": 30},
    # 🔵 4th status — no-answer / pending. Chosen per wireflow step 5.
    {"lead_status": "No Answer", "color": "blue", "type": "On Hold", "position": 40},
]

# Service dropdown options shown in the Service column (scope §3).
SERVICE_OPTIONS = [
    "Website Development",
    "E-commerce Website",
    "Payment Gateway",
    "Admin Dashboard",
    "Mobile App Development",
    "Custom Software",
    "Digital Marketing",
    "IT Services",
    "Maintenance & Support",
    "Other",
]

# Admin roles — full visibility including recordings + delete rights.
ADMIN_ROLES = ["System Manager", "Sales Manager", "Administrator"]

# Agent role — restricted: no recording visibility, no delete rights.
AGENT_ROLE = "Cold Call Agent"

# Call disposition options (Create Call Log page). The officer only picks one
# of these — every other log detail is auto-filled server-side.
# Must stay in sync with api.call_recording.DISPOSITION_OPTIONS.
CALL_DISPOSITIONS = [
    "Interested",
    "Not Interested",
    "Follow-up",
    "No Answer",
    "Meeting Scheduled",
    "Busy",
    "Failed",
    "Call Back",
]


# ---------------------------------------------------------------------------
# PUBLIC ENTRY POINTS (called from hooks.py)
# ---------------------------------------------------------------------------

def after_install():
    """Called once when the app is installed on a site."""
    seed_all()


def after_migrate():
    """Called on every `bench migrate`. Keeps fixtures in sync."""
    seed_all()


def seed_all():
    """Run every seeder in dependency order."""
    ensure_roles()
    ensure_lead_statuses()
    cleanup_legacy_lead_statuses()
    ensure_custom_fields()
    ensure_call_log_custom_fields()
    lock_recording_field()
    ensure_client_scripts()
    ensure_agent_access_defaults()
    ensure_agent_export_block()
    ensure_agent_ui_settings_read()
    ensure_lead_owner_hidden()
    frappe.db.commit()


# ---------------------------------------------------------------------------
# A. ROLES
# ---------------------------------------------------------------------------

def ensure_roles():
    """Create the restricted Agent role if missing.

    Admins reuse the stock System Manager / Sales Manager roles, so we only
    create the low-privilege role here. Desk access is granted so Agents can
    use CRM lists; deletion is blocked separately in api/permissions.py.
    """
    if not frappe.db.exists("Role", AGENT_ROLE):
        role = frappe.new_doc("Role")
        role.role_name = AGENT_ROLE
        role.desk_access = 1
        role.search_bar = 1
        role.notifications = 1
        role.list_sidebar = 1
        role.bulk_actions = 0  # defensive: no bulk delete for agents
        role.insert(ignore_permissions=True)
        frappe.logger().info(f"[crm_coldcall] created Role: {AGENT_ROLE}")


# ---------------------------------------------------------------------------
# B. LEAD STATUSES (4 modes)
# ---------------------------------------------------------------------------

def ensure_lead_statuses():
    """Upsert the 4 pipeline statuses into `CRM Lead Status`.

    Uses lead_status (autoname field) as the natural key so re-runs update
    colour/type/position instead of duplicating.
    """
    for row in LEAD_STATUSES:
        name = row["lead_status"]
        if frappe.db.exists("CRM Lead Status", name):
            frappe.db.set_value("CRM Lead Status", name, "color", row["color"])
            frappe.db.set_value("CRM Lead Status", name, "type", row["type"])
            frappe.db.set_value("CRM Lead Status", name, "position", row["position"])
        else:
            doc = frappe.new_doc("CRM Lead Status")
            doc.lead_status = name
            doc.color = row["color"]
            doc.type = row["type"]
            doc.position = row["position"]
            doc.insert(ignore_permissions=True)


# Legacy statuses removed from the workflow (RBAC spec §5). Existing rows
# are remapped before deletion so no Lead is left pointing at a dead link.
# "New" is intentionally KEPT: the stock CRM controller assigns it to fresh
# leads (guarded by an exists-check) and our normalise hook maps it to
# "No Answer" on save.
LEGACY_LEAD_STATUSES = [
    "Contacted",
    "Nurture",
    "Qualified",
    "Converted",
    "Unqualified",
    "Junk",
]


def cleanup_legacy_lead_statuses():
    """Remap leads off the 6 retired statuses, then delete those rows.

    Idempotent: safe to re-run on every migrate (also heals sites where the
    stock CRM re-seeds its defaults). Never raises — a failure here must not
    break the whole migration.
    """
    try:
        from crm_coldcall.api.lead_pipeline import LEGACY_MAP

        for legacy in LEGACY_LEAD_STATUSES:
            if not frappe.db.exists("CRM Lead Status", legacy):
                continue
            target = LEGACY_MAP.get(legacy)
            if target:
                frappe.db.sql(
                    "UPDATE `tabCRM Lead` SET status = %s WHERE status = %s",
                    (target, legacy),
                )
            try:
                frappe.delete_doc("CRM Lead Status", legacy, ignore_permissions=True)
            except Exception:
                # Referenced elsewhere — leave the row; migrate continues.
                frappe.logger().warning(
                    f"[crm_coldcall] could not delete lead status {legacy}",
                    exc_info=True,
                )
    except Exception:
        frappe.logger().warning(
            "[crm_coldcall] cleanup_legacy_lead_statuses skipped", exc_info=True
        )


# ---------------------------------------------------------------------------
# G. AGENT MODULE ACCESS defaults (RBAC)
# ---------------------------------------------------------------------------

def ensure_agent_access_defaults():
    """Create an all-enabled access row for every agent user missing one.

    Runs on every migrate so pre-existing agents keep working after the
    RBAC upgrade; Admins then restrict per-agent from the UI. Never raises.
    """
    try:
        from crm_coldcall.api.agent_access import AGENT_ROLES, ADMIN_ROLES

        agent_users = frappe.get_all(
            "Has Role",
            filters={"role": ["in", list(AGENT_ROLES)]},
            fields=["parent"],
            limit=500,
        )
        for row in agent_users:
            user = row.get("parent")
            if not user or user in ("Administrator", "Guest"):
                continue
            try:
                roles = set(frappe.get_roles(user))
            except Exception:
                continue
            if roles & set(ADMIN_ROLES):
                continue  # admins need no row
            if frappe.db.exists("Agent Module Access", {"agent_user": user}):
                continue
            try:
                doc = frappe.new_doc("Agent Module Access")
                doc.agent_user = user
                doc.insert(ignore_permissions=True)
            except Exception:
                frappe.logger().warning(
                    f"[crm_coldcall] access-row seeding skipped for {user}",
                    exc_info=True,
                )
    except Exception:
        frappe.logger().warning(
            "[crm_coldcall] ensure_agent_access_defaults skipped", exc_info=True
        )


# ---------------------------------------------------------------------------
# C. CUSTOM FIELDS on CRM Lead
# ---------------------------------------------------------------------------
# We create Custom Fields (not schema edits) so the stock CRM app stays
# upgrade-safe. Field order: PDF block -> outcome block -> services table ->
# requirement/meeting block.

def _custom_field_exists(doctype, fieldname):
    return frappe.db.exists(
        "Custom Field", {"dt": doctype, "fieldname": fieldname}
    )


def _make_custom_field(doctype, *, fieldname, label, fieldtype, **kwargs):
    """Create one Custom Field if missing; update label/options otherwise."""
    if _custom_field_exists(doctype, fieldname):
        return
    cf = frappe.new_doc("Custom Field")
    cf.dt = doctype
    cf.fieldname = fieldname
    cf.label = label
    cf.fieldtype = fieldtype
    for key, value in kwargs.items():
        setattr(cf, key, value)
    cf.insert(ignore_permissions=True)


def ensure_custom_fields():
    """Create every custom field the wireflow needs on CRM Lead."""
    DT = "CRM Lead"

    # --- PDF block: secure upload + machine-readable summary ---------------
    _make_custom_field(
        DT, fieldname="custom_pdf", label="Historical Data PDF",
        fieldtype="Attach", insert_after="lead_name",
        description="Upload a historical client PDF. Server auto-parses it (see Cold Call PDF Import). Private files only.",
        allow_on_submit=1, no_copy=0,
    )
    _make_custom_field(
        DT, fieldname="custom_pdf_summary", label="PDF Extract Summary",
        fieldtype="Small Text", insert_after="custom_pdf", read_only=1, no_copy=1,
        description="Auto-filled by the PDF parser. Do not edit manually.",
    )

    # --- Call outcome block (mirrors wireflow steps 5/6/7) -----------------
    _make_custom_field(
        DT, fieldname="custom_call_outcome", label="Call Outcome",
        fieldtype="Select", insert_after="custom_pdf_summary",
        options="\nNo Answer\nNot Interested\nInterested\nFollow-up\nMeeting Scheduled",
        in_standard_filter=1, in_list_view=1,
        description="Last cold-call result. Drives the 4-mode pipeline tabs.",
    )
    _make_custom_field(
        DT, fieldname="custom_next_followup", label="Next Follow-up",
        fieldtype="Date", insert_after="custom_call_outcome", in_standard_filter=1,
        description="Required when outcome is Follow-up or No Answer.",
    )

    # --- Service table (scope §3: dropdown + description column) -----------
    _make_custom_field(
        DT, fieldname="custom_services_section", label="Services Required",
        fieldtype="Section Break", insert_after="custom_next_followup",
    )
    _make_custom_field(
        DT, fieldname="custom_services", label="Services",
        fieldtype="Table", options="Cold Call Service Item",
        insert_after="custom_services_section",
        description="Pick a Service from the dropdown, then describe the client need in the adjacent Description cell.",
    )

    # --- Requirement / meeting block (wireflow steps 8/9) ------------------
    _make_custom_field(
        DT, fieldname="custom_requirement", label="Requirement Details",
        fieldtype="Small Text", insert_after="custom_services",
    )
    _make_custom_field(
        DT, fieldname="custom_budget", label="Expected Budget",
        fieldtype="Currency", insert_after="custom_requirement",
    )
    _make_custom_field(
        DT, fieldname="custom_timeline", label="Expected Timeline",
        fieldtype="Data", insert_after="custom_budget",
        description="e.g. '3 Months'.",
    )
    _make_custom_field(
        DT, fieldname="custom_meeting_date", label="Meeting Date",
        fieldtype="Date", insert_after="custom_timeline",
    )
    _make_custom_field(
        DT, fieldname="custom_meeting_notes", label="Meeting Notes / Next Steps",
        fieldtype="Small Text", insert_after="custom_meeting_date",
    )


# ---------------------------------------------------------------------------
# C2. CUSTOM FIELDS on CRM Call Log (features 2 + 3)
# ---------------------------------------------------------------------------
# The officer only selects `custom_call_disposition` — everything else is
# auto-filled by api.call_recording.automate_call_log (before_insert).
# Recording is stamped automatically for every call (no agent activation).

def ensure_call_log_custom_fields():
    """Create disposition + auto-log marker fields on CRM Call Log."""
    DT = "CRM Call Log"
    _make_custom_field(
        DT, fieldname="custom_call_disposition", label="Call Disposition",
        fieldtype="Select", insert_after="status",
        options="\n" + "\n".join(CALL_DISPOSITIONS),
        in_standard_filter=1, in_list_view=1,
        description="Pick the call outcome. All other log details are auto-filled; the linked Lead status updates automatically.",
    )
    _make_custom_field(
        DT, fieldname="custom_remarks", label="Disposition Remarks",
        fieldtype="Small Text", insert_after="custom_call_disposition",
        description="Optional: officer types any extra note about the call here.",
    )
    _make_custom_field(
        DT, fieldname="custom_auto_logged", label="Auto Logged",
        fieldtype="Check", insert_after="custom_remarks", read_only=1, no_copy=1,
        description="Set by the server when log details were auto-created.",
    )
    _make_custom_field(
        DT, fieldname="custom_recording_status", label="Recording Status",
        fieldtype="Select", insert_after="custom_auto_logged", read_only=1, no_copy=1,
        options="\nAuto-Recording\nRecording Pending\nAvailable",
        in_standard_filter=1,
        description="Auto-Recording = recording runs automatically for every customer call, no manual activation needed.",
    )


# ---------------------------------------------------------------------------
# D. RECORDING LOCKDOWN (scope §2)
# ---------------------------------------------------------------------------

def lock_recording_field():
    """Move CRM Call Log.recording_url to permlevel 1 (Admin-only).

    Steps:
      1. Set permlevel=1 on the stock DocField (needs a DB commit + cache clear).
      2. Grant permlevel-1 read/write ONLY to ADMIN_ROLES.
      3. Ensure the Agent role keeps permlevel-0 rights (create/call-logging)
         but never sees permlevel 1.

    Field-level hiding is enforced again at runtime in api/permissions.py so
    even `frappe.client.get` cannot leak the URL.

    CRITICAL: once ANY Custom DocPerm exists for a DocType, Frappe ignores
    the standard DocType permissions and uses ONLY Custom DocPerm. So we MUST
    create permlevel-0 rows for every role that needs list/form access,
    otherwise the list API silently drops all level-0 fields (caller,
    receiver, type, status, duration, from, to) and the Call Logs list
    renders blank with 0s durations.
    """
    # 1. Bump the field itself.
    frappe.db.sql(
        "UPDATE `tabDocField` SET permlevel = 1 "
        "WHERE parent = 'CRM Call Log' AND fieldname = 'recording_url'"
    )
    # Also handle the case where a Custom Field override exists.
    frappe.db.sql(
        "UPDATE `tabCustom Field` SET permlevel = 1 "
        "WHERE dt = 'CRM Call Log' AND fieldname = 'recording_url'"
    )

    # 2. Level-0: mirror the stock CRM permissions so the list keeps working.
    # Stock CRM Call Log JSON grants full access to System/Sales Manager and
    # Sales User. Cold Call Agent gets create/read/write but no delete
    # (delete is additionally blocked at runtime in api/permissions.py).
    _ensure_docperm(
        "CRM Call Log", "System Manager", permlevel=0,
        read=1, write=1, create=1, delete=1,
        email=1, export=1, print=1, report=1, share=1,
    )
    _ensure_docperm(
        "CRM Call Log", "Sales Manager", permlevel=0,
        read=1, write=1, create=1, delete=1,
        email=1, export=1, print=1, report=1, share=1,
    )
    _ensure_docperm(
        "CRM Call Log", "Administrator", permlevel=0,
        read=1, write=1, create=1, delete=1,
        email=1, export=1, print=1, report=1, share=1,
    )
    _ensure_docperm(
        "CRM Call Log", "Sales User", permlevel=0,
        read=1, write=1, create=1, delete=1,
        email=1, export=1, print=1, report=1, share=1,
    )
    _ensure_docperm(
        "CRM Call Log", AGENT_ROLE, permlevel=0,
        read=1, write=1, create=1, delete=0,
        email=1, export=1, print=1, report=1, share=1,
    )

    # 3. Level-1 (recording_url): Admin-only.
    for role in ADMIN_ROLES:
        _ensure_docperm("CRM Call Log", role, permlevel=1, read=1, write=1)
    _strip_docperm_level("CRM Call Log", AGENT_ROLE, permlevel=1)
    _strip_docperm_level("CRM Call Log", "Sales User", permlevel=1)

    frappe.clear_cache(doctype="CRM Call Log")


def _ensure_docperm(doctype, role, *, permlevel, read=0, write=0,
                    create=0, delete=0, email=0, export=0, print=0,
                    report=0, share=0):
    """Add a Custom DocPerm row if missing, or top-up an existing row.

    The old version only inserted when missing, which left the system in a
    broken state: level-0 rows were never created, so as soon as a level-1
    row existed Frappe dropped all level-0 fields from get_list. This version
    also updates an existing row so re-running the seeder heals old sites.
    """
    name = frappe.db.exists(
        "Custom DocPerm",
        {"parent": doctype, "role": role, "permlevel": permlevel},
    )
    want = {
        "read": read, "write": write, "create": create, "delete": delete,
        "email": email, "export": export, "print": print,
        "report": report, "share": share,
    }
    if name:
        doc = frappe.get_doc("Custom DocPerm", name)
        changed = False
        for key, value in want.items():
            if doc.get(key) != value and value:
                # Only ever escalate (never strip Admin level-1 here);
                # stripping is done explicitly via _strip_docperm_level.
                doc.set(key, value)
                changed = True
        if changed:
            doc.save(ignore_permissions=True)
        return
    perm = frappe.new_doc("Custom DocPerm")
    perm.parent = doctype
    perm.parenttype = "DocType"
    perm.parentfield = "permissions"
    perm.role = role
    perm.permlevel = permlevel
    for key, value in want.items():
        perm.set(key, value)
    perm.insert(ignore_permissions=True)


def _strip_docperm_level(doctype, role, *, permlevel):
    """Remove any Custom DocPerm that would expose `permlevel` to `role`."""
    for name in frappe.get_all(
        "Custom DocPerm",
        filters={"parent": doctype, "role": role, "permlevel": permlevel},
        pluck="name",
    ):
        frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True)


# ---------------------------------------------------------------------------
# E. CLIENT SCRIPTS (Agent UI hardening + pipeline tabs)
# ---------------------------------------------------------------------------

def ensure_client_scripts():
    """Create three Client Scripts (idempotent, keyed by name).

    1. `coldcall-hide-recording` — hides recording_url/play button for Agents.
    2. `coldcall-pipeline-tabs`  — adds the 4 status shortcut buttons.
    3. `coldcall-auto-log` — on CRM Call Log form: shows the auto-log /
       auto-recording banner and focuses the Disposition field so the
       officer only selects/types the disposition.
    The heavy lifting lives in public/js/lead_pipeline_tabs.js; the scripts
    below are thin loaders so upgrades never orphan inline code.
    """
    _ensure_client_script(
        name="coldcall-hide-recording",
        dt="CRM Call Log",
        script=(
            "// Hide recording controls from non-admins (server still blocks API).\n"
            "// Recording itself is automatic — agents never press record.\n"
            "if (!frappe.user.has_role('System Manager') && !frappe.user.has_role('Sales Manager')) {\n"
            "  frappe.ui.form.on('CRM Call Log', {\n"
            "    refresh(frm) {\n"
            "      frm.set_df_property('recording_url', 'hidden', 1);\n"
            "      frm.set_df_property('recording_url', 'read_only', 1);\n"
            "      frm.set_df_property('custom_recording_status', 'read_only', 1);\n"
            "      frm.set_df_property('custom_auto_logged', 'read_only', 1);\n"
            "      if (!frm.doc.custom_call_disposition) {\n"
            "        frm.scroll_to_field('custom_call_disposition');\n"
            "      }\n"
            "    }\n"
            "  });\n"
        ),
    )
    _ensure_client_script(
        name="coldcall-auto-log",
        dt="CRM Call Log",
        script=(
            "// Auto-log helper: pre-fill new call logs from the linked Lead.\n"
            "// Server hook backfills anything left empty; this is instant UX.\n"
            "frappe.ui.form.on('CRM Call Log', {\n"
            "  onload(frm) {\n"
            "    if (frm.is_new() && !frm.doc.telephony_medium) {\n"
            "      frm.set_value('type', frm.doc.type || 'Outgoing');\n"
            "      frm.set_value('status', frm.doc.status || 'Completed');\n"
            "      frm.set_value('telephony_medium', 'Manual');\n"
            "      frm.set_value('custom_recording_status', 'Auto-Recording');\n"
            "      if (frm.doc.reference_docname && (!frm.doc.to || !frm.doc.from)) {\n"
            "        frappe.call({\n"
            "          method: 'crm_coldcall.api.call_recording.get_call_log_defaults',\n"
            "          args: { reference_doctype: frm.doc.reference_doctype || 'CRM Lead',\n"
            "                  reference_docname: frm.doc.reference_docname },\n"
            "          callback: (r) => {\n"
            "            if (r.message) frm.set_value(r.message);\n"
            "          }\n"
            "        });\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "});\n"
        ),
    )
    _ensure_client_script(
        name="coldcall-pipeline-tabs",
        dt="CRM Lead",
        script=(
            "// Pipeline shortcut filters (mirrors lead_pipeline_tabs.js logic).\n"
            "// Kept tiny on purpose — full tab bar is injected by the app JS.\n"
            "frappe.listview_settings['CRM Lead'] = frappe.listview_settings['CRM Lead'] || {};\n"
            "frappe.listview_settings['CRM Lead'].coldcall_modes = "
            "['Interested', 'Not Interested', 'Follow-up', 'No Answer'];\n"
        ),
    )


def _ensure_client_script(*, name, dt, script):
    if frappe.db.exists("Client Script", name):
        frappe.db.set_value("Client Script", name, "script", script)
        frappe.db.set_value("Client Script", name, "enabled", 1)
        return
    cs = frappe.new_doc("Client Script")
    cs.name = name
    cs.dt = dt
    cs.view = "List"
    cs.enabled = 1
    cs.script = script
    cs.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# H. AGENT EXPORT BLOCK (final matrix: Import ❌ Export ❌ for Agents)
# ---------------------------------------------------------------------------
# export_query -> can_export() only consults ROLE permissions, never
# controller hooks — so the real block must live in Custom DocPerm.
# setup_custom_perms() mirrors stock perms once (safe no-op afterwards);
# we then switch export off for the two agent roles. Admin roles untouched.
# Import needs no change: Data Import is System-Manager-only already.
EXPORT_BLOCKED_DOCTYPES = [
    "CRM Lead",
    "CRM Task",
    "CRM Deal",
    "CRM Contacts",
    "Contact",
    "CRM Organization",
    "CRM Call Log",
    "FCRM Note",
]
EXPORT_BLOCKED_ROLES = ["Sales User", "Cold Call Agent"]

# §2 matrix: agent roles may CREATE only these doctypes (Call Logs; Notes are
# an activity sub-item, not a list module, but agents must keep adding them).
AGENT_CREATE_ALLOWED = {"CRM Call Log", "FCRM Note"}
# Everything else: create=0, delete=0, import=0, export=0 for agent roles.
# Stock CRM shipped Sales User with create=1/delete=1 — that is the bug that
# made frappe.client.get_doc_permissions report create/delete to the SPA, so
# the detail-page trash icon and New buttons kept rendering for agents.
AGENT_CREATE_BLOCKED = [
    "CRM Lead",
    "CRM Task",
    "CRM Deal",
    "CRM Contacts",
    "Contact",
    "CRM Organization",
]


def ensure_agent_export_block():
    """Strip Export + Create/Delete rights for agent roles on agent modules.

    Runs on install and migrate. Admin roles (System Manager / Sales Manager
    / Administrator) are never touched, so Admin keeps full CRUD/Import/Export.
    """
    try:
        from frappe.permissions import setup_custom_perms

        for dt in EXPORT_BLOCKED_DOCTYPES:
            try:
                try:
                    setup_custom_perms(dt)
                except Exception:
                    pass
                for role in EXPORT_BLOCKED_ROLES:
                    allow_create = dt in AGENT_CREATE_ALLOWED
                    _ensure_agent_perm_block(dt, role, allow_create=allow_create)
                _ensure_if_owner_neutral(dt)
            except Exception:
                frappe.logger().warning(
                    f"[crm_coldcall] agent perm block skipped for {dt}", exc_info=True
                )
    except Exception:
        frappe.logger().warning(
            "[crm_coldcall] ensure_agent_export_block skipped", exc_info=True
        )


def _ensure_if_owner_neutral(doctype):
    """Zero create/delete on if_owner Custom DocPerm rows (non-admin roles).

    Stock CRM ships an `All` role row with if_owner create=1/delete=1 on
    Contact. Every user implicitly holds `All`, so an agent who owns a record
    would get create/delete=1 from get_doc_permissions → UI buttons reappear
    for owner documents. Admin roles keep their unconditional perms, so Admin
    behavior is unchanged.
    """
    frappe.db.set_value(
        "Custom DocPerm",
        {
            "parent": doctype,
            "if_owner": 1,
            "role": ["not in", ADMIN_ROLES],
        },
        {"create": 0, "delete": 0, "import": 0, "export": 0},
    )


def _ensure_agent_perm_block(doctype, role, allow_create=False):
    """Force the agent matrix on every Custom DocPerm row for (doctype, role).

    create: 0 unless allow_create (Call Log / Note stay creatable)
    delete: 0 always          import: 0 always       export: 0 always
    read/write stay as-is so View/Edit keep working.
    """
    names = frappe.get_all(
        "Custom DocPerm",
        filters={"parent": doctype, "role": role},
        pluck="name",
    )
    values = {
        "delete": 0,
        "import": 0,
        "export": 0,
        "create": 1 if allow_create else 0,
    }
    if names:
        for name in names:
            frappe.db.set_value("Custom DocPerm", name, values)
        return
    # Role had no Custom DocPerm row yet: mirror a working read/write row
    # with the restricted flags applied.
    perm = frappe.new_doc("Custom DocPerm")
    perm.parent = doctype
    perm.parenttype = "DocType"
    perm.parentfield = "permissions"
    perm.role = role
    perm.permlevel = 0
    perm.read = 1
    perm.write = 1
    perm.create = 1 if allow_create else 0
    perm.delete = 0
    perm.export = 0
    perm.email = 1
    perm.print = 1
    perm.report = 1
    perm.share = 1
    perm.set("import", 0)
    perm.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# J. AGENT READ ACCESS TO FCRM SETTINGS (SPA user menu -> Log out)
# ---------------------------------------------------------------------------
# The CRM SPA builds the sidebar user dropdown (brand + full name, bottom
# of the sidebar) ENTIRELY from FCRM Settings.dropdown_items:
#   stores/settings.js -> createDocumentResource({doctype:'FCRM Settings'})
#   UserDropdown.vue   -> if (!settings.value?.dropdown_items) return []
# A role WITHOUT DocType read gets a 403 on that resource, the settings
# store stays empty and the whole dropdown renders with ZERO items —
# agents then have no "Log out" (support issue: "cannot log out as agent").
# Stock CRM ships read for "Sales User" only; our custom "Cold Call
# Agent" role needs the same read-only grant.
#
# CRITICAL: the first Custom DocPerm row for a doctype makes Frappe ignore
# the stock DocType permissions entirely, so setup_custom_perms() must
# mirror every stock role into Custom DocPerm BEFORE we add our row
# (same pattern as ensure_agent_export_block). Read-only: no write /
# create / delete is ever granted here.

FCRM_SETTINGS_DOCTYPE = "FCRM Settings"


def ensure_agent_ui_settings_read():
    """Grant the Agent role read-only access to FCRM Settings.

    Idempotent + never raises. Re-run on every migrate so the SPA user
    menu (My Settings / About / Log out) always renders for agents.
    """
    try:
        from frappe.permissions import setup_custom_perms

        try:
            setup_custom_perms(FCRM_SETTINGS_DOCTYPE)
        except Exception:
            pass
        _ensure_docperm(
            FCRM_SETTINGS_DOCTYPE, AGENT_ROLE, permlevel=0,
            read=1, email=0, print=0, report=0, share=0,
        )
        frappe.clear_cache(doctype=FCRM_SETTINGS_DOCTYPE)
    except Exception:
        frappe.logger().warning(
            "[crm_coldcall] ensure_agent_ui_settings_read skipped", exc_info=True
        )


# ---------------------------------------------------------------------------
# I. HIDE LEAD ASSIGNMENT UI (no Assign Agent / Lead Owner anywhere)
# ---------------------------------------------------------------------------
# Visibility is view-permission based, so the assignment field is vestigial.
# Hidden via Property Setter (upgrade-safe, no stock JSON edit): Desk forms
# and lists drop it; the SPA overlay hides its own Assign widget.

def ensure_lead_owner_hidden():
    """Hide CRM Lead.lead_owner from forms and lists for every role."""
    try:
        for prop, value in (("hidden", "1"), ("in_list_view", "0")):
            name = f"CRM Lead-lead_owner-{prop}"
            if frappe.db.exists("Property Setter", name):
                frappe.db.set_value("Property Setter", name, "value", value)
                continue
            ps = frappe.new_doc("Property Setter")
            ps.name = name
            ps.doctype_or_field = "DocField"
            ps.doc_type = "CRM Lead"
            ps.field_name = "lead_owner"
            ps.property = prop
            ps.property_type = "Check"
            ps.value = value
            ps.insert(ignore_permissions=True)
    except Exception:
        frappe.logger().warning(
            "[crm_coldcall] ensure_lead_owner_hidden skipped", exc_info=True
        )
