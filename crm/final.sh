#!/bin/bash
# Final build + functional smoke test. Runs INSIDE the container.
cd /home/frappe/frappe-bench
echo "=== build assets ==="
bench build --app crm_coldcall 2>&1 | tail -n 5
bench --site crm.localhost clear-cache 2>&1 | tail -n 2
echo "=== smoke test ==="
./env/bin/python /workspace/smoke.py
