#!/bin/bash
# Runs INSIDE the frappe container — verification only.
cd /home/frappe/frappe-bench
echo "=== doctypes ==="
bench --site crm.localhost mariadb -e "SHOW TABLES LIKE 'tabCold Call%';" 2>&1 | head -n 20
echo "=== custom fields ==="
bench --site crm.localhost mariadb -e "SELECT fieldname, fieldtype, options FROM \`tabCustom Field\` WHERE dt='CRM Lead' AND fieldname LIKE 'custom\_%';" 2>&1 | head -n 40
echo "=== lead statuses ==="
bench --site crm.localhost mariadb -e "SELECT lead_status, color, type FROM \`tabCRM Lead Status\`;" 2>&1 | head -n 20
echo "=== roles ==="
bench --site crm.localhost mariadb -e "SELECT name FROM tabRole WHERE name LIKE '%Cold%';" 2>&1 | head -n 20
