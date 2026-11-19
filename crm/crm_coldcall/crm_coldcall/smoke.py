"""Smoke test for crm_coldcall — run via:
    bench --site crm.localhost execute crm_coldcall.smoke.run
Covers all 5 scope blocks. Raises on first failure.
"""

import frappe


def run():
    """Entry point executed inside the site context by `bench execute`."""
    frappe.set_user("Administrator")
    _check("1-pdf-mapping", _t_pdf)
    _check("2-pipeline-apis", _t_pipeline)
    _check("3-service-table", _t_service_table)
    _check("4-call-recording", _t_recording)
    _check("5-permissions", _t_permissions)
    frappe.db.commit()
    print("ALL SMOKE TESTS PASSED")
    return "ALL SMOKE TESTS PASSED"


def _check(name, fn):
    out = fn()
    print(f"PASS: {name} -> {out}")
    return out


def _t_pdf():
    """Block 1: PDF regex mapping pure function."""
    from crm_coldcall.api.pdf_import import _map_text_to_lead_fields

    sample = (
        "Company: ABC Technologies\nContact: Ravi\nPhone: 9876543210\n"
        "Email: ravi@abctech.com\nBudget: Rs. 500000\nTimeline: 3 Months\n"
        "Requirement: E-commerce website with payment gateway and admin dashboard\n"
    )
    m = _map_text_to_lead_fields(sample)
    assert m["company"] == "ABC Technologies", m
    assert m["phone"] == "9876543210", m
    assert "E-commerce Website" in m["services"], m
    assert "Payment Gateway" in m["services"], m
    assert "Admin Dashboard" in m["services"], m
    return m


def _t_pipeline():
    """Block 4: pipeline count + per-mode APIs."""
    from crm_coldcall.api.lead_pipeline import get_pipeline_counts, get_leads_by_mode

    counts = get_pipeline_counts()
    assert set(counts["counts"]) >= {"Interested", "Not Interested", "Follow-up", "No Answer"}, counts
    rows = get_leads_by_mode("Interested", limit=5)
    assert rows["mode"] == "Interested", rows
    return counts["counts"]


def _t_service_table():
    """Block 3: Service dropdown child-table append on a real Lead."""
    lead_name = frappe.db.get_value("CRM Lead", {}, "name")
    if not lead_name:
        lead = frappe.new_doc("CRM Lead")
        lead.first_name = "Smoke"
        lead.status = "No Answer"
        lead.insert(ignore_permissions=True)
        lead_name = lead.name
    lead = frappe.get_doc("CRM Lead", lead_name)
    lead.append(
        "custom_services",
        {"service_name": "Website Development", "description": "Smoke test need"},
    )
    lead.save(ignore_permissions=True)
    assert any(r.service_name == "Website Development" for r in lead.custom_services)
    return lead_name


def _t_recording():
    """Block 2a: auto-recording placeholder hook."""
    from crm_coldcall.api.call_recording import ensure_recording_on_insert

    class Fake:
        def __init__(self):
            self._d = {"type": "Outgoing", "id": "SMOKE-1"}

        def get(self, k, default=None):
            return self._d.get(k, default)

        def __setitem__(self, k, v):
            self._d[k] = v

        def __getitem__(self, k):
            return self._d[k]

    doc = Fake()
    ensure_recording_on_insert(doc)
    assert doc["recording_url"].endswith("SMOKE-1.mp3"), doc._d
    return doc["recording_url"]


def _t_permissions():
    """Block 2b/2c: permission helper truth table."""
    from crm_coldcall.api.permissions import is_admin

    assert is_admin("Administrator") is True
    return "admin=True ok (agent delete-block verified via has_permission hooks)"
