#!/bin/bash
# Runs INSIDE the frappe container.
set -e
cd /home/frappe/frappe-bench
echo "=== 1. cleaning half-created custom fields ==="
bench --site crm.localhost mariadb < /workspace/clean.sql || true
echo "=== 2. syncing doctypes (creates Cold Call tables first) ==="
bench --site crm.localhost migrate
echo "=== 3. installing app (runs after_install seeder) ==="
bench --site crm.localhost install-app crm_coldcall || bench --site crm.localhost migrate
echo "=== 4. running seeder explicitly (idempotent) ==="
bench --site crm.localhost execute crm_coldcall.install.seed_all
echo "=== DONE ==="
