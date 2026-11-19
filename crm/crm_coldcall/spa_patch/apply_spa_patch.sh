#!/bin/bash
# Re-applies the crm_coldcall SPA RBAC overlay onto the container's CRM app
# and rebuilds the frontend. Runs INSIDE the frappe container.
#
#   docker exec crm-frappe-1 bash -c 'bash /workspace/crm_coldcall/spa_patch/apply_spa_patch.sh'
#
# Re-run after every fresh bench install / CRM app update (apps/ is ephemeral).
set -e
CRM_FE=/home/frappe/frappe-bench/apps/crm/frontend
SRC=/workspace/crm_coldcall/spa_patch/rbac.js

if [ ! -f "$SRC" ]; then
  echo "patch source missing: $SRC"
  exit 1
fi

cp "$SRC" "$CRM_FE/src/utils/rbac.js"

# Idempotent main.js hook: import + init (runs for every visitor; no-op for Admins).
if ! grep -q "utils/rbac" "$CRM_FE/src/main.js"; then
  python3 - "$CRM_FE/src/main.js" <<'EOF'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace(
    "import App from './App.vue'",
    "import App from './App.vue'\nimport { initRbacUi } from './utils/rbac'",
)
s = s.replace(
    "app.mount('#app')",
    "try { initRbacUi() } catch (e) { /* rbac overlay best-effort */ }\n  app.mount('#app')",
)
open(p, "w").write(s)
EOF
  echo "main.js hooked"
else
  echo "main.js already hooked"
fi

cd "$CRM_FE"
yarn build
echo "SPA rebuilt with RBAC overlay"
