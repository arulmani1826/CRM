"""Plain-python smoke (no DB/site). Import-safe checks for blocks 1, 2a, 2b."""

from crm_coldcall.api.pdf_import import _map_text_to_lead_fields

m = _map_text_to_lead_fields(
    "Company: ABC Technologies\nPhone: 9876543210\n"
    "Requirement: payment gateway and mobile app\n"
)
assert m["company"] == "ABC Technologies", m
assert "Payment Gateway" in m["services"], m
print("PASS pdf-pure:", m)

from crm_coldcall.api.call_recording import ensure_recording_on_insert


class FakeDoc(dict):
    """Mimics frappe Document attribute access for the hook test."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


doc = FakeDoc(type="Outgoing", id="SMOKE-1")
ensure_recording_on_insert(doc)
assert doc["recording_url"].endswith("SMOKE-1.mp3"), dict(doc)
print("PASS recording:", doc["recording_url"])

# Incoming calls are stamped too (auto-record EVERY call, hooks.py).
doc2 = FakeDoc(type="Incoming", id="INC-1")
ensure_recording_on_insert(doc2)
assert doc2["recording_url"].endswith("INC-1.mp3"), dict(doc2)
print("PASS recording-incoming-stamped")

from crm_coldcall.api.permissions import is_admin

assert is_admin("Administrator") is True
print("PASS permissions-admin")

from crm_coldcall.api.lead_pipeline import MODES, LEGACY_MAP

assert MODES == ["Interested", "Not Interested", "Follow-up", "No Answer"], MODES
assert LEGACY_MAP["New"] == "No Answer", LEGACY_MAP
print("PASS pipeline-modes:", MODES)
print("ALL PLAIN SMOKE TESTS PASSED")
