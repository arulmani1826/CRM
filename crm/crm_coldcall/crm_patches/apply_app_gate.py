#!/usr/bin/env python3
"""App-access gate patch: let the `Cold Call Agent` role load the CRM SPA.

Problem: crm.api.check_app_permission() gates the whole /crm page on
`role in ["System Manager", "Sales User", "Sales Manager"]`. A restricted
agent holding only the custom `Cold Call Agent` role fails that check, so
/crm returns HTTP 403 and the agent can never load the CRM frontend at all
(the "agent can't see anything / every button glitches" root cause).

What it does (idempotent): injects "Cold Call Agent" into that allowed-roles
list so restricted agents pass the app gate. Actual module/record permission
is still enforced downstream by crm_coldcall (has_permission + query
conditions + guard_create), so this only opens the front door — it grants no
extra data access. Falls back gracefully if the CRM file shape changes
(upstream anchor missing -> SystemExit, never a silent no-op).

Stored in crm_coldcall (source of truth). Applied to the container by:
  docker exec crm-frappe-1 bash -c \
    'cd /home/frappe/frappe-bench && ./env/bin/python \
      /workspace/crm_coldcall/crm_patches/apply_app_gate.py'
Re-apply after every fresh bench install / CRM app update (apps/ is ephemeral).
"""
import pathlib

TARGET = pathlib.Path("/home/frappe/frappe-bench/apps/crm/crm/api/__init__.py")

# The stock allowed-roles tuple inside check_app_permission().
OLD = '\tif any(role in ["System Manager", "Sales User", "Sales Manager"] for role in roles):\n\t\treturn True\n'
NEW = (
    '\t# crm_coldcall RBAC: allow the restricted Cold Call Agent role through\n'
    '\t# the app gate so agents can load /crm. Data access is still enforced by\n'
    '\t# crm_coldcall permissions (has_permission / query conditions / guards).\n'
    '\tif any(\n'
    '\t\trole in ["System Manager", "Sales User", "Sales Manager", "Cold Call Agent"]\n'
    '\t\tfor role in roles\n'
    '\t):\n'
    '\t\treturn True\n'
)


def main():
    src = TARGET.read_text()
    if '"Cold Call Agent"' in src and "crm_coldcall RBAC" in src:
        print("app-gate patch already applied")
        return
    if OLD not in src:
        raise SystemExit("anchor not found - CRM check_app_permission changed upstream")
    TARGET.write_text(src.replace(OLD, NEW, 1))
    print("app-gate patch applied (Cold Call Agent allowed through /crm gate)")


if __name__ == "__main__":
    main()
