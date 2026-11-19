#!/bin/bash
# Plain-python checks (no site context needed) — runs INSIDE container.
cd /home/frappe/frappe-bench
./env/bin/python /workspace/plain_smoke.py
