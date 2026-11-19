"""Controller for `Agent Module Access`.

One row per Agent user, maintained by Admins. Read at runtime by
api.agent_access (db.get_value, no permission checks) so Agents can never
escalate their own modules: the DocType grants Agents read-only.
"""

import frappe
from frappe.model.document import Document


class AgentModuleAccess(Document):
    """Per-agent module toggles + record scope."""

    def validate(self):
        """Only Admins may create/modify access rows (backend, not just UI)."""
        from crm_coldcall.api.agent_access import is_admin

        if not is_admin():
            frappe.throw(
                "You do not have permission to modify agent permissions.",
                frappe.PermissionError,
            )
