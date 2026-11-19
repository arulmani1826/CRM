"""Controller for child table `Cold Call Service Item`.

Child tables need no server logic — this stub exists so Frappe can resolve
`crm_coldcall.doctype.cold_call_service_item...` and so future validation
(e.g. budget > 0) has a home.
"""

import frappe
from frappe.model.document import Document


class ColdCallServiceItem(Document):
    """One Service dropdown row + adjacent Description cell (scope §3)."""

    pass
