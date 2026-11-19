"""RBAC verification harness — real HTTP calls as Admin and Agent.

Runs INSIDE the frappe container. Logs in as both users via the web API,
then exercises the permission matrix (create/delete/import/export/convert/
call-log/lead-visibility) and prints PASS/FAIL per requirement.

Usage:
  cd /home/frappe/frappe-bench && ./env/bin/python /workspace/rbac_test.py
"""
import json
import re
import urllib.request
import urllib.parse
import urllib.error

BASE = "http://127.0.0.1:8000"
ADMIN = ("Administrator", "admin")
AGENT = ("agent1@example.com", "ColdCall@2026")

_results = []


def _opener():
    jar = urllib.request.HTTPCookieProcessor()
    return urllib.request.build_opener(jar), jar


def _csrf_from(jar):
    for c in jar.cookiejar:
        if c.name == "csrf_token":
            return c.value
    return None


def login(username, password):
    """Log in and return (opener, csrf_token)."""
    op, jar = _opener()
    op.open(BASE + "/login")
    payload = json.dumps({"usr": username, "pwd": password}).encode()
    req = urllib.request.Request(
        BASE + "/api/method/login",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    resp = op.open(req)
    body = json.loads(resp.read())
    msg = body.get("message")
    # System users without a portal app return "No App"; that's still a
    # successful login (sid cookie is set). Only treat explicit errors as fail.
    if msg in ("Incorrect password", "Login failed") or (
        isinstance(msg, str) and msg.startswith("Authentication")
    ):
        raise RuntimeError(f"login failed: {body}")
    # CSRF token is embedded in the /crm boot object (behind auth).
    csrf = None
    try:
        html = op.open(BASE + "/crm").read().decode(errors="replace")
        m = re.search(r'window\["csrf_token"\]\s*=\s*"([^"]+)"', html)
        csrf = m.group(1) if m else None
    except Exception:
        csrf = _csrf_from(jar)
    return op, csrf


def call(op, csrf, method, args=None, http="POST"):
    """Call a whitelisted method. Returns (status_code, parsed_json_or_text)."""
    args = args or {}
    # frappe.client.insert/delete expect the doc nested under a "doc" key.
    if method == "frappe.client.insert" and "doctype" in args and "doc" not in args:
        args = {"doc": args}
    elif method == "frappe.client.delete" and "doctype" in args and "doc" not in args:
        args = {"doctype": args["doctype"], "name": args["name"]}
    url = BASE + "/api/method/" + method
    headers = {"Accept": "application/json"}
    if csrf:
        headers["X-Frappe-CSRF-Token"] = csrf
    if http == "GET":
        url += "?" + urllib.parse.urlencode(args)
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        data = json.dumps(args).encode()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        resp = op.open(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    _results.append((status, label, detail))
    print(f"[{status}] {label}" + (f"  -- {detail}" if detail else ""))


def main():
    print("Logging in as Admin + Agent ...")
    aop, acsrf = login(*ADMIN)
    gop, gcsrf = login(*AGENT)
    print("  admin csrf:", bool(acsrf), " agent csrf:", bool(gcsrf))

    # Sanity: both logged in.
    st, me = call(aop, acsrf, "frappe.auth.get_logged_user", http="GET")
    check("Admin logged in", st == 200 and "Administrator" in str(me), str(me)[:80])
    st, me = call(gop, gcsrf, "frappe.auth.get_logged_user", http="GET")
    check("Agent logged in", st == 200 and "agent1" in str(me), str(me)[:80])

    # ------------------------------------------------------------------
    # get_my_modules — role classification
    # ------------------------------------------------------------------
    st, mods = call(aop, acsrf, "crm_coldcall.api.agent_access.get_my_modules", http="GET")
    m = mods.get("message", {})
    check("Admin flagged is_admin", m.get("is_admin") is True, str(m)[:120])
    st, mods = call(gop, gcsrf, "crm_coldcall.api.agent_access.get_my_modules", http="GET")
    m = mods.get("message", {})
    check("Agent flagged is_agent", m.get("is_agent") is True, str(m)[:120])
    check("Agent NOT is_admin", m.get("is_admin") is False)
    # All modules enabled by default
    lm = m.get("modules", {}).get("leads", {})
    check("Agent has leads.view", lm.get("view") is True, str(lm))

    # ------------------------------------------------------------------
    # 3. LEAD VISIBILITY — admin-created lead visible to agent
    # ------------------------------------------------------------------
    st, lead = call(aop, acsrf, "frappe.client.insert", {
        "doctype": "CRM Lead", "first_name": "Admin", "last_name": "Probe",
        "lead_name": "Admin Visibility Probe", "status": "New",
        "lead_owner": "Administrator", "mobile_no": "5550001111"})
    lead_name = lead.get("message", {}).get("name") if st == 200 else None
    check("Admin created a Lead", st == 200 and lead_name, f"{lead_name} (HTTP {st})")

    # Agent lists leads — must include the admin-created one.
    st, lst = call(gop, gcsrf, "frappe.client.get_list", {
        "doctype": "CRM Lead", "fields": ["name"], "limit_page_length": 100,
    })
    names = [d["name"] for d in lst.get("message", [])] if st == 200 else []
    check("Agent LEAD LIST includes admin lead",
          lead_name in names,
          f"HTTP {st}; got {len(names)} leads")

    # Agent opens the lead form directly.
    st, doc = call(gop, gcsrf, "frappe.client.get", {
        "doctype": "CRM Lead", "name": lead_name,
    }, http="GET")
    check("Agent can OPEN admin lead (form)", st == 200, f"HTTP {st} {str(doc)[:80]}")

    # ------------------------------------------------------------------
    # 3b. CREATE restrictions (backend 403)
    # ------------------------------------------------------------------
    for dt, payload in [
        ("CRM Lead", {"lead_name": "Agent Rogue Lead", "status": "New"}),
        ("CRM Deal", {"organization_name": "Rogue Org"}),
        ("CRM Task", {"subject": "Rogue Task"}),
        ("CRM Contacts", {"first_name": "Rogue"}),
        ("CRM Organization", {"organization_name": "Rogue Org"}),
    ]:
        payload["doctype"] = dt
        st, out = call(gop, gcsrf, "frappe.client.insert", payload)
        check(f"Agent CREATE {dt} blocked (403)", st in (401, 403, 417),
              f"HTTP {st} {str(out)[:70]}")

    # Admin can still create a lead (control).
    st, out = call(aop, acsrf, "frappe.client.insert",
                   {"doctype": "CRM Lead", "first_name": "Admin", "lead_name": "Admin Control", "status": "New"})
    check("Admin CREATE CRM Lead allowed", st == 200, f"HTTP {st} {str(out)[:80]}")

    # ------------------------------------------------------------------
    # 5. CALL LOGS — agent CAN create
    # ------------------------------------------------------------------
    st, out = call(gop, gcsrf, "frappe.client.insert", {
        "doctype": "CRM Call Log",
        "type": "Outgoing",
        "status": "Completed",
        "telephony_medium": "Manual",
        "from": "0000000000",
        "to": "1111111111",
        "custom_call_disposition": "Interested",
        "reference_doctype": "CRM Lead",
        "reference_docname": lead_name,
    })
    calllog_name = out.get("message", {}).get("name") if isinstance(out, dict) else None
    check("Agent CAN create Call Log", st == 200 and calllog_name,
          f"HTTP {st} {str(out)[:80]}")

    # ------------------------------------------------------------------
    # 4. LEAD -> DEAL CONVERSION by agent
    # ------------------------------------------------------------------
    st, out = call(gop, gcsrf,
                   "crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal",
                   {"lead": lead_name, "if_converted": "Create"})
    deal = out.get("message") if isinstance(out, dict) else None
    check("Agent can CONVERT lead -> deal", st == 200 and deal,
          f"HTTP {st} {str(out)[:120]}")

    # Duplicate prevention: convert again -> should return existing, not a 2nd deal.
    st2, out2 = call(gop, gcsrf,
                     "crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal",
                     {"lead": lead_name, "if_converted": "Return Existing"})
    dup_ok = (out2.get("message") == deal) if isinstance(out2, dict) else False
    n_deals = frappe_count(aop, acsrf, "CRM Deal", {"lead": lead_name})
    check("No duplicate Deal on re-convert", n_deals == 1 and dup_ok,
          f"deals for lead={n_deals}; ret={str(out2)[:60]}")

    # ------------------------------------------------------------------
    # DELETE restrictions (backend 403) — reuse admin lead + call log
    # ------------------------------------------------------------------
    for dt, name in [("CRM Lead", lead_name), ("CRM Call Log", calllog_name)]:
        if not name:
            continue
        st, out = call(gop, gcsrf, "frappe.client.delete",
                       {"doctype": dt, "name": name})
        check(f"Agent DELETE {dt} blocked", st in (401, 403, 417),
              f"HTTP {st} {str(out)[:70]}")

    # ------------------------------------------------------------------
    # EXPORT / IMPORT restrictions
    # ------------------------------------------------------------------
    st, out = call(gop, gcsrf, "frappe.core.doctype.data_export.data_export.export_json",
                   {"doctype": "CRM Lead", "select_columns": ["`tabCRM Lead`.name"]})
    check("Agent EXPORT leads blocked", st in (401, 403, 417, 404),
          f"HTTP {st} {str(out)[:70]}")
    # Direct DocPerm read: agent should not have export/create/delete on Lead.
    st, perms = call(gop, gcsrf, "frappe.client.get_doc_permissions",
                     {"doctype": "CRM Lead"}, http="GET")
    p = perms.get("message", {}) if isinstance(perms, dict) else {}
    check("Agent Lead perms: no create/delete/export",
          not p.get("create") and not p.get("delete") and not p.get("export"),
          str(p)[:160])

    # ------------------------------------------------------------------
    # 6/7. Deal has correct org + agent can view/edit it
    # ------------------------------------------------------------------
    if deal:
        st, ddoc = call(gop, gcsrf, "frappe.client.get",
                        {"doctype": "CRM Deal", "name": deal}, http="GET")
        check("Agent can OPEN converted Deal", st == 200, f"HTTP {st}")
        st, edit = call(gop, gcsrf, "frappe.client.set_value",
                        {"doctype": "CRM Deal", "name": deal,
                         "fieldname": "deal_owner", "value": ""})
        check("Agent can EDIT Deal (write)", st == 200, f"HTTP {st}")

    print("\n" + "=" * 70)
    fails = [r for r in _results if r[0] == "FAIL"]
    print(f"TOTAL {len(_results)}  PASS {len(_results)-len(fails)}  FAIL {len(fails)}")
    for s, l, d in fails:
        print(f"  FAILED: {l}  -- {d}")
    print("=" * 70)


def frappe_count(aop, acsrf, doctype, filters):
    """Count records via the Admin session using get_list (robust)."""
    st, out = call(aop, acsrf, "frappe.client.get_list", {
        "doctype": doctype, "filters": filters, "limit_page_length": 100,
    })
    if st == 200 and isinstance(out, dict):
        return len(out.get("message") or [])
    return -1


if __name__ == "__main__":
    main()
