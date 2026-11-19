"""Controller for `Cold Call Audit Log`.

Append-only trail of Agent actions + blocked attempts. Written server-side
with ignore_permissions (Agents have no write access to this DocType), so
rows cannot be forged or deleted by Agents.
"""

from frappe.model.document import Document


class ColdCallAuditLog(Document):
    """Immutable-by-policy audit row (Admins manage retention)."""
