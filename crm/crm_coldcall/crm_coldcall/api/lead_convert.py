"""Agent-safe Lead -> Deal conversion (override of the stock whitelisted method).

Requirement: any Agent can convert a client's lead to a deal WITHOUT a
person being set on it. The stock flow does two "person" things:

  1. create_deal copies lead_owner -> deal_owner (cleared for Agents by
     agent_access.strip_conversion_owner, CRM Deal before_insert — that
     also skips the stock after_insert share/assign of deal_owner), and
  2. after insert it assigns every lead assignee onto the new Deal
     (ToDo + DocShare for users without access), stamping people into
     `_assign`.

This wrapper keeps stock behaviour otherwise and, for a conversion that
actually creates a deal, drops (2): open ToDos and DocShare rows on the
brand-new deal held by anyone other than the caller. The caller keeps
their own assignment if they had one. Admin conversions are untouched
(is_restricted_agent guard).

Wired via hooks.override_whitelisted_methods, so the Convert modal, the
bulk "Convert" action and direct API calls all pass through here. Runs
in the same request as the conversion, so the deal it cleans up was
created by THIS call (Return Existing on a pre-existing deal is skipped).
"""

import frappe

from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal as _stock_convert_to_deal

from crm_coldcall.api.agent_access import is_restricted_agent


@frappe.whitelist()
def convert_to_deal(
    lead: str,
    doc=None,
    deal=None,
    existing_contact=None,
    existing_organization=None,
    if_converted: str = "Create",
):
    """Drop-in replacement for the stock method (same signature + return)."""
    existing = frappe.db.get_value("CRM Deal", {"lead": lead}, "name")
    # Only a deal created by THIS call may be cleaned: a "Return Existing"
    # replay hands back an older (possibly Admin-created, assigned) deal.
    creates_new = not existing or if_converted == "Create"

    result = _stock_convert_to_deal(
        lead,
        doc=doc,
        deal=deal,
        existing_contact=existing_contact,
        existing_organization=existing_organization,
        if_converted=if_converted,
    )

    if (
        creates_new
        and is_restricted_agent()
        and isinstance(result, str)
        and result != existing
        and frappe.db.exists("CRM Deal", result)
    ):
        _drop_foreign_assignments(result)
        # Stock crm.api.todo.after_insert mirrors the newest ToDo assignment
        # into deal_owner via db.set_value AFTER our before_insert strip —
        # including a carry-over ToDo held by the converting agent themself
        # (foreign ones are cancelled above, which stock also clears). Re-clear
        # so an agent conversion NEVER ends with a person set on the deal.
        frappe.db.set_value(
            "CRM Deal", result, "deal_owner", None, update_modified=False
        )

    return result


def _drop_foreign_assignments(deal_name: str) -> None:
    """Cancel open ToDos + DocShares on `deal_name` held by anyone but the caller."""
    user = frappe.session.user

    foreign_todos = frappe.get_all(
        "ToDo",
        filters={
            "reference_type": "CRM Deal",
            "reference_name": deal_name,
            "status": "Open",
            "allocated_to": ("!=", user),
        },
        pluck="name",
    )
    for todo in foreign_todos:
        # Saving (not deleting) keeps the assignment comment trail and
        # recomputes the deal's `_assign` via ToDo.on_update.
        doc = frappe.get_doc("ToDo", todo)
        doc.status = "Cancelled"
        doc.save(ignore_permissions=True)

    foreign_shares = frappe.get_all(
        "DocShare",
        filters={
            "share_doctype": "CRM Deal",
            "share_name": deal_name,
            "user": ("!=", user),
        },
        pluck="user",
    )
    for share_user in foreign_shares:
        frappe.share.remove("CRM Deal", deal_name, share_user)
