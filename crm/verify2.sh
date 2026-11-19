#!/bin/bash
cd /home/frappe/frappe-bench
bench --site crm.localhost mariadb -e "SELECT name, module, istable FROM tabDocType WHERE name LIKE 'Cold Call%';"
echo "--- all tables like cold ---"
bench --site crm.localhost mariadb -e "SHOW TABLES;" | grep -i cold || echo "NO COLD TABLES"
