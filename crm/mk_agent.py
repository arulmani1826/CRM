import frappe


def run(username="agent1", password="agent123", role="Cold Call Agent"):
    """Create an agent user + the Agent Module Access row (idempotent)."""
    email = f"{username}@example.com"

    if not frappe.db.exists("User", email):
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = "Cold"
        user.last_name = "Agent"
        user.username = username
        user.enabled = 1
        user.user_type = "System User"
        user.new_password = password
        user.insert(ignore_permissions=True)
        print(f"Created user: {email}")
    else:
        print(f"User already exists: {email}")

    # Grant the restricted agent role (separate child-doc insert).
    if not frappe.db.exists("Has Role", {"parent": email, "role": role}):
        frappe.get_doc(
            {
                "doctype": "Has Role",
                "parent": email,
                "parenttype": "User",
                "parentfield": "roles",
                "role": role,
            }
        ).insert(ignore_permissions=True)
        print(f"Granted role: {role}")

    # Seed the Agent Module Access row (all modules enabled by default).
    if not frappe.db.exists("Agent Module Access", {"agent_user": email}):
        try:
            doc = frappe.new_doc("Agent Module Access")
            doc.agent_user = email
            doc.insert(ignore_permissions=True)
            print("Seeded Agent Module Access row.")
        except Exception as e:
            print(f"(module access row skipped: {e})")

    frappe.db.commit()
    roles = frappe.get_roles(email)
    print("ROLES ->", roles)
    print("LOGIN ->", email, "/", password)
