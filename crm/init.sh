#!/bin/bash
# Frappe CRM + Cold-Call customizations — container entrypoint.
# Runs as user `frappe` with working_dir /home/frappe. /workspace is the
# host mount (~/frappe-crm) so crm_coldcall source survives restarts.
#
# Layout:
#   /workspace/crm_coldcall  -> custom app source of truth (edit here from WSL)
#   ~/frappe-bench           -> ephemeral bench (rebuilt if wiped)
set -e
cd /home/frappe

# ---------------------------------------------------------------------------
# PATH A: bench already exists (normal restart). Reinstall workspace app so
# code edits apply, migrate, then start. Never wipes the DB here.
# ---------------------------------------------------------------------------
if [ -d "/home/frappe/frappe-bench/apps/frappe" ]; then
    echo "Bench exists — syncing workspace app and starting."
    cd frappe-bench
    if [ -d "/workspace/crm_coldcall" ]; then
        echo "Installing crm_coldcall from /workspace..."
        rm -rf apps/crm_coldcall
        cp -r /workspace/crm_coldcall apps/crm_coldcall
        ./env/bin/pip install -q -e apps/crm_coldcall || true
        ./env/bin/pip install -q pypdf || true
        bench --site crm.localhost install-app crm_coldcall || true
        bench --site crm.localhost migrate || true
        bench build --app crm_coldcall || true
    fi
    bench start
    exit 0
fi

# ---------------------------------------------------------------------------
# PATH B: fresh bench (first run, or container rebuilt).
# NEVER wipes an existing database: adopts it if present (see DATA-GUARD).
# ---------------------------------------------------------------------------
echo "Creating new bench..."
# Try to init, if network fails, try without version pinning or with different approach
bench init --skip-redis-config-generation frappe-bench --version version-15 || {
  echo "Retrying bench init..."
  rm -rf frappe-bench 2>/dev/null || true
  bench init --skip-redis-config-generation frappe-bench --skip-redis-config-generation || true
}

cd frappe-bench

# Use containers instead of localhost
bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis:6379
bench set-redis-queue-host redis://redis:6379
bench set-redis-socketio-host redis://redis:6379

# Remove redis, watch from Procfile
sed -i '/redis/d' ./Procfile
sed -i '/watch/d' ./Procfile

bench get-app whatsapp
bench get-app crm --branch main

# Custom app from the host mount (keeps PDF/recording/pipeline features).
if [ -d "/workspace/crm_coldcall" ]; then
    echo "Installing crm_coldcall from /workspace..."
    cp -r /workspace/crm_coldcall apps/crm_coldcall
    ./env/bin/pip install -q -e apps/crm_coldcall || true
    ./env/bin/pip install -q pypdf || true
fi

# ---------------------------------------------------------------------------
# DATA-GUARD: adopt an existing database instead of wiping it.
# A rebuilt container has an empty bench, but mariadb-data may still hold
# the site DB. `new-site --force` would DROP it (total data loss). So:
#   1. back up everything to /workspace/backups (host mount, survives all),
#   2. if a Frappe DB exists -> adopt it (write site_config, migrate, start),
#   3. only if NO database exists -> fresh new-site (with --force for retry).
# ---------------------------------------------------------------------------
echo "Backing up any existing databases to /workspace/backups..."
bash /workspace/backups/backup_now.sh pre-install || true

echo "Waiting for MariaDB..."
for i in $(seq 1 30); do
    mysql -h mariadb -uroot -p123 -N -e "SELECT 1;" 2>/dev/null && break
    sleep 2
done || true

existing_db=""
dbs=$(mysql -h mariadb -uroot -p123 -N -e "SHOW DATABASES;" 2>/dev/null || true)
for db in $dbs; do
    case "$db" in
        information_schema|mysql|performance_schema|sys) continue ;;
    esac
    tables=$(mysql -h mariadb -uroot -p123 -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$db' AND table_name='tabDocType';" 2>/dev/null || echo 0)
    if [ "${tables:-0}" -gt 0 ] 2>/dev/null; then
        existing_db="$db"
        break
    fi
done

if [ -n "$existing_db" ]; then
    echo "Existing site database found ($existing_db) — adopting it, NOT wiping."
    # Full site skeleton: bench commands AND the running server need logs/.
    mkdir -p sites/crm.localhost/public/files sites/crm.localhost/private/files sites/crm.localhost/private/backups sites/crm.localhost/locks sites/crm.localhost/logs
    cat > sites/crm.localhost/site_config.json <<EOF
{
  "db_name": "$existing_db",
  "db_password": "123",
  "developer_mode": 1,
  "mute_emails": 1,
  "server_script_enabled": 1
}
EOF
    # The wiped bench lost the original random DB-user password; as root we
    # reset it to match site_config so the adopted site can connect.
    mysql -h mariadb -uroot -p123 -e "ALTER USER '$existing_db'@'%' IDENTIFIED BY '123'; FLUSH PRIVILEGES;" 2>/dev/null || true
    bench use crm.localhost
    # Apps were fetched above; make sure the workspace app is registered.
    if [ -d "apps/crm_coldcall" ]; then
        ./env/bin/pip install -q -e apps/crm_coldcall || true
        bench --site crm.localhost install-app crm_coldcall || true
    fi
    bench --site crm.localhost migrate || true
    bench build --app crm_coldcall || true
    bench --site crm.localhost clear-cache || true
    bench start
    exit 0
fi

echo "No existing database — fresh install."
export MYSQL_SSL_MODE=DISABLED
export MYSQL_CLIENT_SSL_MODE=DISABLED
bench new-site crm.localhost \
    --force \
    --mariadb-root-password 123 \
    --admin-password admin \
    --mariadb-user-host-login-scope='%' \
    --db-host mariadb

bench --site crm.localhost install-app crm
# Install custom app AFTER crm so its DocTypes + seeders layer on top.
if [ -d "apps/crm_coldcall" ]; then
    bench --site crm.localhost install-app crm_coldcall || true
fi
bench --site crm.localhost set-config developer_mode 1
bench --site crm.localhost set-config mute_emails 1
bench --site crm.localhost set-config server_script_enabled 1
bench --site crm.localhost clear-cache
bench use crm.localhost

bench start
