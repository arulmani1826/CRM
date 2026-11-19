"""Agent role helpers + per-module access control (RBAC core).

Single source of truth for:
  * who is an Admin vs a restricted Agent,
  * which modules (leads/tasks/deals/organizations/call_logs/contacts)
    an Agent may see,
  * whether an Agent is limited to assigned records or may see all,
  * hard create-guards (before_insert) so Agents can never create records
    even if the UI is bypassed. Deal conversion still works because the
    server-side convert flow inserts with ignore_permissions (skipped here).

Reads the `Agent Module Access` DocType with db.get_value (bypasses
permissions, no recursion). Missing row => all modules ON, assigned-only
scope (backward compatible for pre-existing agents).
"""

import frappe

# ---------------------------------------------------------------------------
# TUNABLES — keep in sync with api/permissions.py + install.py
# ---------------------------------------------------------------------------
ADMIN_ROLES = {"Administrator", "System Manager", "Sales Manager"}
AGENT_ROLES = {"Cold Call Agent", "Sales User"}

MODULES = ("leads", "tasks", "deals", "contacts", "organizations", "call_logs")

# CRM DocType -> module key (None = not module-gated, e.g. notes).
DOCTYPE_MODULE = {
    "CRM Lead": "leads",
    "CRM Task": "tasks",
    "CRM Deal": "deals",
    "CRM Contacts": "contacts",
    "Contact": "contacts",
    "CRM Organization": "organizations",
    "CRM Call Log": "call_logs",
    "FCRM Note": None,
}

# DocTypes a restricted Agent may NEVER create directly (backend-enforced).
# CRM Call Log is intentionally allowed: logging customer calls IS the
# agent's job (View/Create/Edit ✅, Delete ❌). FCRM Note stays creatable
# too. Internal server flows that insert with ignore_permissions (e.g.
# Convert to Deal) bypass this guard via the flags check in guard_create.
CREATE_BLOCKED = {
    "CRM Lead",
    "CRM Task",
    "CRM Deal",
    "CRM Contacts",
    "Contact",
    "CRM Organization",
}

_ENABLE_FIELD = {
    "leads": "enable_leads",
    "tasks": "enable_tasks",
    "deals": "enable_deals",
    "call_logs": "enable_call_logs",
    "contacts": "enable_contacts",
    "organizations": "enable_organizations",
}
_VIEW_ALL_FIELD = {
    "leads": "view_all_leads",
    "tasks": "view_all_tasks",
    "deals": "view_all_deals",
    "call_logs": "view_all_call_logs",
    "contacts": "view_all_contacts",
    "organizations": "view_all_organizations",
}

_DEFAULT_ACCESS = {
    "enable_leads": 1,
    "enable_tasks": 1,
    "enable_deals": 1,
    "enable_call_logs": 1,
    "enable_contacts": 1,
    "enable_organizations": 1,
    "view_all_leads": 0,
    "view_all_tasks": 0,
    "view_all_deals": 0,
    "view_all_call_logs": 0,
    "view_all_contacts": 0,
    "view_all_organizations": 0,
}


# ---------------------------------------------------------------------------
# ROLE HELPERS
# ---------------------------------------------------------------------------

def _roles(user=None) -> set:
    """Role set for a user (defaults to session user)."""
    user = user or frappe.session.user
    if user == "Administrator":
        return {"Administrator", "System Manager"}
    try:
        return set(frappe.get_roles(user))
    except Exception:
        return set()


def is_admin(user=None) -> bool:
    """True when user holds any admin role."""
    return bool(_roles(user) & ADMIN_ROLES)


def is_restricted_agent(user=None) -> bool:
    """True when user is an agent WITHOUT admin rights."""
    roles = _roles(user)
    return bool(roles & AGENT_ROLES) and not (roles & ADMIN_ROLES)


# ---------------------------------------------------------------------------
# ACCESS-ROW READ
# ---------------------------------------------------------------------------

def get_agent_access(user=None) -> dict:
    """Merged access dict for `user` (defaults + stored row)."""
    user = user or frappe.session.user
    access = dict(_DEFAULT_ACCESS)
    try:
        row = frappe.db.get_value(
            "Agent Module Access",
            {"agent_user": user},
            ["enable_leads", "enable_tasks", "enable_deals",
             "enable_call_logs", "enable_contacts", "enable_organizations",
             "view_all_leads", "view_all_tasks", "view_all_deals",
             "view_all_call_logs", "view_all_contacts",
             "view_all_organizations"],
            as_dict=True,
        )
    except Exception:
        row = None
    if row:
        for key in access:
            if row.get(key) is not None:
                access[key] = row.get(key)
    return access


def module_enabled(user, module: str) -> bool:
    """True when `user` may access `module` (admins always True)."""
    if is_admin(user):
        return True
    if module not in _ENABLE_FIELD:
        return True
    return bool(get_agent_access(user).get(_ENABLE_FIELD[module]))


def may_view_all(user, module: str) -> bool:
    """True when `user` may see every record in `module`."""
    if is_admin(user):
        return True
    if module not in _VIEW_ALL_FIELD:
        return True
    return bool(get_agent_access(user).get(_VIEW_ALL_FIELD[module]))


# ---------------------------------------------------------------------------
# RECORD-LEVEL CHECK (assigned vs all)
# ---------------------------------------------------------------------------

def _doc_val(doc, field):
    try:
        return doc.get(field) if doc is not None else None
    except Exception:
        return None


def can_access_record(user, doctype: str, doc) -> bool:
    """True when `user` may open `doc` (ownership rule).

    Admins: always True. Agents with view_all: True. Otherwise the user
    must be the assignee/creator. New (unsaved) docs skip this check —
    create permission is decided separately.
    """
    if is_admin(user):
        return True
    module = DOCTYPE_MODULE.get(doctype)
    if module and may_view_all(user, module):
        return True
    if doc is None or _doc_val(doc, "name") is None and not _doc_val(doc, "owner"):
        return True
    if doctype == "CRM Lead":
        # Final spec: visibility is view-permission only — no owner/
        # assignment filtering. The module gate lives in has_permission.
        return True
    if doctype == "CRM Deal":
        return _doc_val(doc, "deal_owner") == user or _doc_val(doc, "owner") == user
    if doctype == "CRM Call Log":
        return user in (
            _doc_val(doc, "caller"),
            _doc_val(doc, "receiver"),
            _doc_val(doc, "owner"),
        )
    if doctype in ("CRM Contacts", "Contact", "CRM Task",
                     "CRM Organization", "FCRM Note"):
        return _doc_val(doc, "owner") == user
    return True


# ---------------------------------------------------------------------------
# HARD CREATE GUARD (doc_events before_insert — backend, not UI)
# ---------------------------------------------------------------------------

def guard_create(doc, method=None):
    """Throw 403 when a restricted Agent tries to create a blocked DocType.

    Skipped for server-side inserts that run with ignore_permissions (the
    Convert to Deal flow creates its Deal/Contact/Organization that way,
    and Deal conversion must keep working for Agents). Direct UI/API
    creates never carry that flag, so they stay blocked.
    """
    try:
        user = frappe.session.user
    except Exception:
        return
    if not is_restricted_agent(user):
        return
    try:
        if getattr(getattr(doc, "flags", None), "ignore_permissions", False):
            return
    except Exception:
        pass
    try:
        if frappe.flags.ignore_permissions:
            return
    except Exception:
        pass
    if doc.doctype in CREATE_BLOCKED:
        from crm_coldcall.api.audit_log import log_denied

        singular = {
            "CRM Lead": "leads",
            "CRM Task": "tasks",
            "CRM Deal": "deals",
            "CRM Contacts": "contacts",
            "Contact": "contacts",
            "CRM Organization": "organizations",
            "CRM Call Log": "call logs",
        }.get(doc.doctype, "records")
        log_denied(f"Create {doc.doctype}", doc.doctype, None,
                   f"Agent does not have create permission for {singular}.")
        frappe.throw(
            "Agents are not allowed to create records.",
            frappe.PermissionError,
        )


# ---------------------------------------------------------------------------
# CONVERSION: never stamp a person on an agent-converted Deal
# ---------------------------------------------------------------------------

def strip_conversion_owner(doc, method=None):
    """Leave deal_owner empty when a restricted Agent converts Lead -> Deal.

    Requirement: any agent can convert a client's lead to a deal WITHOUT a
    person being set on it. Stock create_deal copies lead_owner ->
    deal_owner (and stock after_insert then shares/assigns that person),
    so we clear the field here — before_insert runs for every insert,
    including the conversion's insert(ignore_permissions=True), and also
    when the caller passed deal_owner inside the `deal` payload.
    Admins are untouched: they keep stock behaviour (person set).
    """
    try:
        user = frappe.session.user
    except Exception:
        return
    if not is_restricted_agent(user):
        return
    if not doc.get("lead"):
        return
    doc.deal_owner = None


# ---------------------------------------------------------------------------
# WHITELISTED APIS (Admin UI + role-based frontend)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_my_modules():
    """Return the caller's module visibility for the role-based UI."""
    user = frappe.session.user
    admin = is_admin(user)
    return {
        "is_admin": admin,
        "is_agent": is_restricted_agent(user),
        "modules": {
            module: {
                "view": module_enabled(user, module),
                "view_all": may_view_all(user, module),
            }
            for module in MODULES
        },
    }


@frappe.whitelist()
def set_agent_access(agent_user: str, **flags):
    """Create/update an Agent's module row (Admin only).

    Flags: enable_leads/tasks/deals/call_logs/contacts/organizations,
    view_all_* (0/1).
    """
    if not is_admin():
        frappe.throw(
            "You do not have permission to assign agent permissions.",
            frappe.PermissionError,
        )
    if not frappe.db.exists("User", agent_user):
        frappe.throw(f"User {agent_user} does not exist.")
    name = frappe.db.exists("Agent Module Access", {"agent_user": agent_user})
    if name:
        doc = frappe.get_doc("Agent Module Access", name)
    else:
        doc = frappe.new_doc("Agent Module Access")
        doc.agent_user = agent_user
    allowed = set(_DEFAULT_ACCESS)
    for key, value in (flags or {}).items():
        if key in allowed:
            doc.set(key, int(bool(int(value))) if str(value).isdigit() else int(bool(value)))
    doc.save(ignore_permissions=True)
    return {"ok": True, "agent_user": agent_user}
