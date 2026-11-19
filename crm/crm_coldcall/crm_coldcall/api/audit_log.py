"""Audit logging for Agent actions (RBAC §10).

`log_action` / `log_denied` never raise and bypass permissions, so logging
can never break a save nor be forged by Agents (who have no write access
to `Cold Call Audit Log`). Called from:
  * api/permissions.py — blocked create/delete + disabled-module access,
  * api/audit actors — after_insert/on_update of agent writes (Allowed).
"""

import frappe

AUDIT_DOCTYPE = "Cold Call Audit Log"


def log_action(action: str, ref_doctype=None, ref_name=None,
               result="Allowed", detail=None, user=None):
    """Append one audit row. Never raises."""
    try:
        user = user or frappe.session.user
        if not user or user == "Guest":
            return
        if not frappe.db.exists("DocType", AUDIT_DOCTYPE):
            return
        doc = frappe.new_doc(AUDIT_DOCTYPE)
        doc.agent = user
        doc.action = (action or "")[:140]
        doc.result = result if result in ("Allowed", "Denied") else "Allowed"
        if ref_doctype:
            doc.ref_doctype = ref_doctype
        if ref_name:
            doc.ref_name = ref_name
        if detail:
            doc.detail = detail
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.logger().warning("[crm_coldcall] audit log skipped", exc_info=True)


def log_denied(action: str, ref_doctype=None, ref_name=None, detail=None, user=None):
    """Shortcut for blocked attempts (Result = Denied)."""
    log_action(action, ref_doctype, ref_name, result="Denied", detail=detail, user=user)


def audit_write(doc, method=None):
    """Doc hook: record an Agent's successful create/update (Allowed).

    Wired as after_insert/on_update for the 4 CRM modules. Admin/system
    writes are ignored to keep the trail Agent-focused.
    """
    try:
        from crm_coldcall.api.agent_access import is_restricted_agent

        user = frappe.session.user
        if not is_restricted_agent(user):
            return
        verb = "Created" if method == "after_insert" else "Updated"
        log_action(
            f"{verb} {doc.doctype}",
            doc.doctype,
            getattr(doc, "name", None),
            result="Allowed",
            detail=f"{doc.doctype} {getattr(doc, 'name', '')} by {user}",
        )
    except Exception:
        frappe.logger().warning("[crm_coldcall] audit_write skipped", exc_info=True)
