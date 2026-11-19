"""Controller for audit DocType `Cold Call PDF Import`.

Stores one row per PDF parse: source file, target lead, extracted JSON,
final status. Agents may create but never delete (see permissions in JSON
+ api/permissions.py); only Admins delete.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class ColdCallPDFImport(Document):
    """Audit row for Smart PDF Processing (scope §1)."""

    def before_insert(self):
        """Stamp who/when for the audit trail."""
        self.parsed_on = now_datetime()
        if frappe.session.user != "Guest":
            self.parsed_by = frappe.session.user
