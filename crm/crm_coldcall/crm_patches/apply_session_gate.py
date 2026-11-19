#!/usr/bin/env python3
"""Session/role-gate patch: let `Cold Call Agent` into the CRM user set.

Problem: crm.api.session hardcodes two role lists that gate the SPA:

  * CRM_ALLOWED_ROLES = ["System Manager", "Sales Manager", "Sales User"]
      -> get_session_role_flags() THROWS PermissionError for any role not in
         it, which fails the whole crm.api.session.get_users call (the SPA's
         first request). Even after the /crm page gate is fixed (see
         apply_app_gate.py), the frontend router's isCrmUser() checks
         users.data.crmUsers, which is built from this same list. A restricted
         agent holding only the custom `Cold Call Agent` role is therefore
         treated as a non-CRM user and the router redirects to the
         "Access Denied / Not Permitted" screen.

  * role_priority = ("System Manager", "Sales Manager", "Sales User", "Guest")
      -> resolves each user's display `role`; without Cold Call Agent the
         agent's role falls through to "" (or Guest) and is dropped from
         crm_users by the `if user.role in CRM_ALLOWED_ROLES` filter below.

What it does (idempotent): injects "Cold Call Agent" into both lists so
restricted agents resolve into crmUsers and pass isCrmUser(). It grants no
extra data access — record/module permission is still enforced downstream by
crm_coldcall (has_permission + query conditions + guard_create). Falls back
with SystemExit (never a silent no-op) if the CRM file shape changes upstream.

Stored in crm_coldcall (source of truth). Applied to the container by:
  docker exec crm-frappe-1 bash -c \
    'cd /home/frappe/frappe-bench && ./env/bin/python \
      /workspace/crm_coldcall/crm_patches/apply_session_gate.py'
Re-apply after every fresh bench install / CRM app update (apps/ is ephemeral).
"""
import pathlib

TARGET = pathlib.Path("/home/frappe/frappe-bench/apps/crm/crm/api/session.py")

OLD_ROLES = 'CRM_ALLOWED_ROLES = ["System Manager", "Sales Manager", "Sales User"]\n'
NEW_ROLES = (
    "# crm_coldcall RBAC: allow the restricted Cold Call Agent role into the\n"
    "# CRM user set so agents pass isCrmUser() / get_session_role_flags().\n"
    "# Data access is still enforced by crm_coldcall permissions.\n"
    'CRM_ALLOWED_ROLES = ["System Manager", "Sales Manager", "Sales User", "Cold Call Agent"]\n'
)

OLD_PRIORITY = '\trole_priority = ("System Manager", "Sales Manager", "Sales User", "Guest")\n'
NEW_PRIORITY = (
    '\t# crm_coldcall RBAC: resolve the agent role so it survives the\n'
    '\t# `if user.role in CRM_ALLOWED_ROLES` filter below.\n'
    '\trole_priority = (\n'
    '\t\t"System Manager",\n'
    '\t\t"Sales Manager",\n'
    '\t\t"Sales User",\n'
    '\t\t"Cold Call Agent",\n'
    '\t\t"Guest",\n'
    '\t)\n'
)


def main():
    src = TARGET.read_text()
    changed = False
    if '"Cold Call Agent"]' not in src:
        if OLD_ROLES not in src:
            raise SystemExit("anchor not found - CRM_ALLOWED_ROLES changed upstream")
        src = src.replace(OLD_ROLES, NEW_ROLES, 1)
        changed = True
    if '"Cold Call Agent",\n\t\t"Guest",' not in src and '"Cold Call Agent", ' not in src:
        if OLD_PRIORITY not in src:
            raise SystemExit("anchor not found - role_priority changed upstream")
        src = src.replace(OLD_PRIORITY, NEW_PRIORITY, 1)
        changed = True
    TARGET.write_text(src)
    print("session-gate patch applied" if changed else "session-gate patch already applied")


if __name__ == "__main__":
    main()
