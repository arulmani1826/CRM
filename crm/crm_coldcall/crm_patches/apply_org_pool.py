#!/usr/bin/env python3
"""Lead/Deal-visibility patch for the stock CRM org-hierarchy gate.

Problem: crm.permissions.org_hierarchy denies agents every Lead whose
lead_owner != them (form) and filters such rows from lists — hiding
Admin-created leads. Same gate scopes Deals by deal_owner/ToDo, which
hides a converted deal from the agent who just converted it (converted
deals carry an empty deal_owner for agents). Final spec: any agent with
Leads-view sees ALL leads; any agent with Deals-view is scoped by the
crm_coldcall layer only (deal_owner/owner).

What it does (idempotent): in _permission_query_conditions()' default
(Sales User) branch, CRM-Lead rows defer to crm_coldcall's module flag —
view enabled returns no filter — and likewise for CRM-Deal rows with the
deals flag. Falls back to stock behavior if our app is missing
(try/except). Deal/other branches and explicit hierarchy trees (in_tree)
are untouched. _has_permission() reuses the same conditions, so one edit
fixes both form and list paths.

Stored in crm_coldcall (source of truth). Applied to the container by:
  docker exec crm-frappe-1 bash -c \
    'cd /home/frappe/frappe-bench && ./env/bin/python /workspace/crm_coldcall/crm_patches/apply_org_pool.py'
Re-apply after every fresh bench install / CRM app update (apps/ is ephemeral).
"""
import pathlib

TARGET = pathlib.Path(
    "/home/frappe/frappe-bench/apps/crm/crm/permissions/org_hierarchy.py"
)
ANCHOR = "\t# Sales User default: own records and records directly assigned to them\n"
OLD_Q1 = "\tq1 = DT[owner_field] == user\n"
# Superseded v1 pool block (removed on upgrade).
OLD_POOL = (
    "\t# Sales User default: own records, records directly assigned to them,\n"
    "\t# and the unassigned pool (no owner yet) so Admin-created leads stay\n"
    "\t# visible to agents with module access (crm_coldcall RBAC). Lead-only;\n"
    "\t# explicit hierarchy trees (in_tree branch above) are untouched.\n"
    "\tq1 = DT[owner_field] == user\n"
    "\tif doctype == \"CRM Lead\":\n"
    "\t\tq1 = q1 | DT[owner_field].isnull() | (DT[owner_field] == \"\")\n"
)
NEW_BLOCK = (
    "\t# Sales User default: own records and records directly assigned to them.\n"
    "\t# crm_coldcall RBAC is authoritative for LEAD visibility (Lead-only):\n"
    "\t# agents with Leads-view see every lead; the coldcall app enforces the\n"
    "\t# module flag plus create/delete itself. Falls back to stock scoping\n"
    "\t# when crm_coldcall is absent. Hierarchy trees (in_tree) untouched.\n"
    "\tq1 = DT[owner_field] == user\n"
    "\tif doctype == \"CRM Lead\":\n"
    "\t\ttry:\n"
    "\t\t\tfrom crm_coldcall.api.agent_access import module_enabled as _cc_leads_view\n"
    "\t\t\tif _cc_leads_view(user, \"leads\"):\n"
    "\t\t\t\treturn \"\"\n"
    "\t\texcept Exception:\n"
    "\t\t\tpass\n"
)

# v2: CRM-Deal rows defer to the coldcall layer too (inserted right after
# the lead block above). Marker: "crm_coldcall RBAC is authoritative for
# DEAL visibility".
LEAD_BLOCK = (
    "\tif doctype == \"CRM Lead\":\n"
    "\t\ttry:\n"
    "\t\t\tfrom crm_coldcall.api.agent_access import module_enabled as _cc_leads_view\n"
    "\t\t\tif _cc_leads_view(user, \"leads\"):\n"
    "\t\t\t\treturn \"\"\n"
    "\t\texcept Exception:\n"
    "\t\t\tpass\n"
)
DEAL_BLOCK = LEAD_BLOCK + (
    "\t# crm_coldcall RBAC is authoritative for DEAL visibility as well:\n"
    "\t# agents with Deals-view are scoped by the coldcall layer only\n"
    "\t# (deal_owner/owner; an agent-converted deal has an empty deal_owner),\n"
    "\t# so stock deal_owner/ToDo scoping is skipped. Falls back to stock\n"
    "\t# scoping when crm_coldcall is absent or the module is disabled.\n"
    "\t# Hierarchy trees (in_tree) untouched.\n"
    "\tif doctype == \"CRM Deal\":\n"
    "\t\ttry:\n"
    "\t\t\tfrom crm_coldcall.api.agent_access import module_enabled as _cc_deals_view\n"
    "\t\t\tif _cc_deals_view(user, \"deals\"):\n"
    "\t\t\t\treturn \"\"\n"
    "\t\texcept Exception:\n"
    "\t\t\tpass\n"
)


def main():
    src = TARGET.read_text()
    changed = False
    if "crm_coldcall RBAC is authoritative" not in src:
        if OLD_POOL in src:
            src = src.replace(OLD_POOL, NEW_BLOCK, 1)
        elif ANCHOR + OLD_Q1 in src:
            src = src.replace(ANCHOR + OLD_Q1, NEW_BLOCK, 1)
        else:
            raise SystemExit("anchor not found - CRM org_hierarchy.py changed upstream")
        changed = True
    # v2: extend the same default branch to CRM Deal rows (list + form).
    if "crm_coldcall RBAC is authoritative for DEAL visibility" not in src:
        if LEAD_BLOCK not in src:
            raise SystemExit("lead block missing - CRM org_hierarchy.py changed upstream")
        src = src.replace(LEAD_BLOCK, DEAL_BLOCK, 1)
        changed = True
    # _has_permission() feeds the conditions object into qb.where(): a plain
    # "" (no filter) crashes pypika. Short-circuit to allowed instead —
    # the coldcall layer already enforced the module flag.
    hp_old = ("\tconditions = _permission_query_conditions(user, doctype)\n"
              "\tDT = frappe.qb.DocType(doctype)\n")
    hp_new = ("\tconditions = _permission_query_conditions(user, doctype)\n"
              "\tif isinstance(conditions, str):\n"
              "\t\t# No row filter (crm_coldcall RBAC decided): doc passes this layer.\n"
              "\t\treturn True\n"
              "\tDT = frappe.qb.DocType(doctype)\n")
    if "No row filter (crm_coldcall RBAC decided)" not in src:
        if hp_old not in src:
            raise SystemExit("has_permission anchor missing - CRM file changed upstream")
        src = src.replace(hp_old, hp_new, 1)
        changed = True
    TARGET.write_text(src)
    print("lead/deal-visibility patch applied" if changed else "lead/deal-visibility patch already applied")


if __name__ == "__main__":
    main()
