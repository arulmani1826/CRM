"""Permissions & data-integrity guard (RBAC core).

Policy (backend-enforced, never UI-only):
  * ADMINS (Administrator / System Manager / Sales Manager) — full access.
  * RESTRICTED AGENTS (Cold Call Agent / Sales User without an admin role):
      - DELETE: never (all guarded DocTypes) -> False + audit DENIED.
      - CREATE: never for CRM Lead / CRM Deal / CRM Contacts / Contact
        -> False + audit DENIED. Call Logs + Notes stay creatable because
        logging calls/notes IS the agent's job (also hard-blocked/allowed
        in api.agent_access.guard_create as defence in depth).
      - READ/WRITE: only when the Admin enabled the module for that agent
        (Agent Module Access) AND the record is assigned to them — unless
        the Admin granted view-all. Exceptions (module-view rule, same as
        each other): CRM Lead and CRM Organization are readable/editable
        by every agent with the module enabled — leads are the agent's
        work surface, orgs are Link/conversion targets. Denials are
        audited.
      - RECORDING VISIBILITY unchanged: `recording_url` stays permlevel 1
        (install.lock_recording_field); this hook never leaks the URL.

Role helpers live in api.agent_access (single source of truth) and are
re-exported here so existing imports keep working.
"""

import frappe

from crm_coldcall.api.agent_access import (
    AGENT_ROLES,
    ADMIN_ROLES,
    CREATE_BLOCKED,
    DOCTYPE_MODULE,
    _roles,
    can_access_record,
    is_admin,
    is_restricted_agent,
    module_enabled,
)
from crm_coldcall.api.audit_log import log_denied

# Backwards-compat alias (older docstrings referenced this name).
DELETE_GUARDED = {"CRM Call Log", "CRM Lead", "CRM Task", "CRM Deal",
                  "CRM Contacts", "Contact", "CRM Organization", "FCRM Note"}

__all__ = [
    "ADMIN_ROLES",
    "AGENT_ROLES",
    "DELETE_GUARDED",
    "is_admin",
    "is_restricted_agent",
    "call_log_has_permission",
    "lead_has_permission",
    "task_has_permission",
    "deal_has_permission",
    "contact_has_permission",
    "organization_has_permission",
    "note_has_permission",
    "call_log_query_conditions",
    "lead_query_conditions",
    "task_query_conditions",
    "deal_query_conditions",
    "contacts_query_conditions",
    "organization_query_conditions",
    "redact_recording_url",
]


# ---------------------------------------------------------------------------
# CORE GUARD — every has_permission hook funnels through here.
# Signature: (doc, user, ptype) -> True / False / None.
# Returning None defers to the standard role-permission engine.
# ---------------------------------------------------------------------------

def _guard(doctype, doc, user=None, ptype="read"):
    """Enforce the Agent matrix for one access check."""
    user = user or frappe.session.user
    if not is_restricted_agent(user):
        return None

    ref = None
    try:
        ref = doc.get("name") if doc is not None else None
    except Exception:
        ref = None

    # --- DELETE: agents never delete -------------------------------------
    if ptype == "delete":
        log_denied(f"Delete {doctype}", doctype, ref,
                   "Agents are not allowed to delete records.", user=user)
        return False

    # --- CREATE: blocked for every agent module EXCEPT Call Logs + Notes.
    # Agents must be able to log customer calls (View/Create/Edit, no
    # Delete). Convert to Deal still works: it inserts with
    # ignore_permissions, skipping guard_create. -------------------------
    if ptype == "create" and doctype in CREATE_BLOCKED:
        singular = {
            "CRM Lead": "leads",
            "CRM Task": "tasks",
            "CRM Deal": "deals",
            "CRM Contacts": "contacts",
            "Contact": "contacts",
            "CRM Organization": "organizations",
            "CRM Call Log": "call logs",
        }.get(doctype, "records")
        log_denied(f"Create {doctype}", doctype, ref,
                   f"Agent does not have create permission for {singular}.", user=user)
        return False

    # --- IMPORT / EXPORT: agents never import or export -------------------
    # (Final matrix: Import ❌ Export ❌ on every agent module.) The main
    # export gate (export_query -> can_export) is role-based, so the real
    # block lives in Custom DocPerm (install.ensure_agent_export_block);
    # this denial covers any endpoint that consults controller hooks.
    if ptype in ("import", "export"):
        log_denied(f"{ptype.capitalize()} {doctype}", doctype, ref,
                   f"Agents are not allowed to {ptype} records.", user=user)
        return False

    # --- READ/WRITE -------------------------------------------------------
    # Leads: pure view-permission rule (final spec) — any existing lead is
    # visible/editable to every agent with Leads-view. No created_by /
    # assigned_to / owner filtering. Returned True here short-circuits the
    # stock CRM hierarchy hook (ours runs first), which would otherwise
    # re-impose assignment scoping.
    if doctype == "CRM Lead" and ptype in ("read", "write"):
        from crm_coldcall.api.agent_access import module_enabled as _leads_view

        if _leads_view(user, "leads"):
            return True
        log_denied(f"{ptype.capitalize()} {doctype}", doctype, ref,
                   "Module 'leads' is not enabled for this agent.", user=user)
        return False

    # --- READ/WRITE: Organizations follow the same module-view rule ------
    # Orgs are Link targets from leads/deals and part of the conversion
    # flow (org picker, deal side panel), so every agent with
    # Organizations-view must be able to open them — owner-scoping here
    # produced 403 toasts ("does not have access to this document:
    # CRM Organization - ...") during/right after converting a lead.
    # Create/delete stay blocked (CREATE_BLOCKED + guard_create above).
    if doctype == "CRM Organization" and ptype in ("read", "write"):
        from crm_coldcall.api.agent_access import module_enabled as _org_view

        if _org_view(user, "organizations"):
            return True
        log_denied(f"{ptype.capitalize()} {doctype}", doctype, ref,
                   "Module 'organizations' is not enabled for this agent.",
                   user=user)
        return False

    # --- READ/WRITE: module must be enabled for this agent ----------------
    if ptype in ("read", "write"):
        module = DOCTYPE_MODULE.get(doctype)
        if module and not module_enabled(user, module):
            log_denied(f"{ptype.capitalize()} {doctype}", doctype, ref,
                       f"Module '{module}' is not enabled for this agent.", user=user)
            return False
        # --- Ownership: assigned records only (unless view-all) -----------
        if not can_access_record(user, doctype, doc):
            log_denied(f"{ptype.capitalize()} {doctype}", doctype, ref,
                       "Record is not assigned to this agent.", user=user)
            return False

    return None


# --- Per-DocType wrappers (wired in hooks.py) ------------------------------

def call_log_has_permission(doc, user=None, ptype="read"):
    """Guard CRM Call Log (delete + module + ownership)."""
    return _guard("CRM Call Log", doc, user, ptype)


def lead_has_permission(doc, user=None, ptype="read"):
    """Guard CRM Lead (create/delete + module + ownership)."""
    return _guard("CRM Lead", doc, user, ptype)


def task_has_permission(doc, user=None, ptype="read"):
    """Guard CRM Task (create/delete + module + ownership)."""
    return _guard("CRM Task", doc, user, ptype)


def deal_has_permission(doc, user=None, ptype="read"):
    """Guard CRM Deal (create/delete + module + ownership)."""
    return _guard("CRM Deal", doc, user, ptype)


def contact_has_permission(doc, user=None, ptype="read"):
    """Guard CRM Contacts + Contact (create/delete + module + ownership)."""
    doctype = "CRM Contacts"
    try:
        if doc is not None and getattr(doc, "doctype", None) == "Contact":
            doctype = "Contact"
    except Exception:
        pass
    return _guard(doctype, doc, user, ptype)


def note_has_permission(doc, user=None, ptype="read"):
    """Guard FCRM Note (agents cannot delete notes)."""
    return _guard("FCRM Note", doc, user, ptype)


def organization_has_permission(doc, user=None, ptype="read"):
    """Guard CRM Organization (create/delete + module + ownership)."""
    return _guard("CRM Organization", doc, user, ptype)


# ---------------------------------------------------------------------------
# QUERY CONDITIONS (row-level filtering for Agent list views).
# Returning "" means "no extra filter" (admin / view-all path).
# ---------------------------------------------------------------------------

def call_log_query_conditions(user=None):
    """Agents see only their own calls unless granted view-all."""
    from crm_coldcall.api.agent_access import may_view_all

    user = user or frappe.session.user
    if is_admin(user) or may_view_all(user, "call_logs"):
        return ""
    safe_user = frappe.db.escape(user)
    return (
        "(`tabCRM Call Log`.caller = {u} OR `tabCRM Call Log`.receiver = {u} "
        "OR `tabCRM Call Log`.owner = {u})"
    ).format(u=safe_user)


def lead_query_conditions(user=None):
    """Which leads an Agent may list (final spec: view-permission only).

    Admins and agents with Leads-view: no filter ("") — every lead in the
    database is listed. The stock CRM list filter is neutralised for these
    users by the crm_patches org-pool patch, which defers to the same flag.
    Agents WITHOUT Leads-view get an impossible condition (empty list);
    opening a form is additionally denied by has_permission.
    """
    from crm_coldcall.api.agent_access import module_enabled as _leads_view

    user = user or frappe.session.user
    if is_admin(user) or _leads_view(user, "leads"):
        return ""
    return "1 = 0"


def deal_query_conditions(user=None):
    """Agents see only assigned deals unless granted view-all."""
    from crm_coldcall.api.agent_access import may_view_all

    user = user or frappe.session.user
    if is_admin(user) or may_view_all(user, "deals"):
        return ""
    safe_user = frappe.db.escape(user)
    return (
        "(`tabCRM Deal`.deal_owner = {u} OR `tabCRM Deal`.owner = {u})"
    ).format(u=safe_user)


def task_query_conditions(user=None):
    """Agents see only their own tasks unless granted view-all."""
    from crm_coldcall.api.agent_access import may_view_all

    user = user or frappe.session.user
    if is_admin(user) or may_view_all(user, "tasks"):
        return ""
    safe_user = frappe.db.escape(user)
    return f"(`tabCRM Task`.owner = {safe_user})"


def organization_query_conditions(user=None):
    """Which orgs an Agent may list (module-view rule, same as leads).

    Orgs are navigation targets from every lead/deal (Link fields) and are
    touched by the conversion flow, so any agent with Organizations-view
    sees all of them — owner-scoping hid linked orgs and broke "convert a
    lead" end-to-end. Agents WITHOUT the module get an empty list;
    opening a form is additionally denied by has_permission.
    """
    from crm_coldcall.api.agent_access import module_enabled as _org_view

    user = user or frappe.session.user
    if is_admin(user) or _org_view(user, "organizations"):
        return ""
    return "1 = 0"


def contacts_query_conditions(user=None):
    """Agents see only their own contacts unless granted view-all."""
    from crm_coldcall.api.agent_access import may_view_all

    user = user or frappe.session.user
    if is_admin(user) or may_view_all(user, "contacts"):
        return ""
    safe_user = frappe.db.escape(user)
    # NOTE: wired for "CRM Contacts". The stock "Contact" list uses owner.
    return f"(`tabCRM Contacts`.owner = {safe_user})"


def contact_query_conditions(user=None):
    """Same scoping for the stock Contact DocType."""
    from crm_coldcall.api.agent_access import may_view_all

    user = user or frappe.session.user
    if is_admin(user) or may_view_all(user, "contacts"):
        return ""
    safe_user = frappe.db.escape(user)
    return f"(`tabContact`.owner = {safe_user})"


# ---------------------------------------------------------------------------
# RUNTIME FIELD REDACTION (defence-in-depth for API reads)
# ---------------------------------------------------------------------------

def redact_recording_url(doc):
    """Strip recording_url in-memory for non-admins.

    Call this from any custom list API before returning rows. The permlevel
    system already hides the field on forms; this covers raw `frappe.get_list`
    projections that explicitly request the column.
    """
    if not is_admin():
        doc.pop("recording_url", None)
    return doc
