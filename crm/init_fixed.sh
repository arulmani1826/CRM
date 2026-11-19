#!/bin/bash
set -e
cd /home/frappe

# ---------------------------------------------------------------------------
# The frappe/bench image ships MariaDB client 11.x whose Connector/C 3.4+
# REQUIRES SSL by default. Our MariaDB server runs with SSL disabled, so every
# `mysql ...` shell call from Frappe (DbManager restore etc.) dies with
#   "ERROR 2026 (HY000): TLS/SSL error: SSL is required, but the server does not support it"
# A client-side ~/.my.cnf with ssl=0 turns that off for all mysql invocations.
# ---------------------------------------------------------------------------
cat > /home/frappe/.my.cnf <<'EOF'
[client]
ssl=0
skip-ssl
EOF
chmod 600 /home/frappe/.my.cnf

# Retry helper for flaky network (git clone / yarn / pip).
retry() {
    local n=0 max=5 delay=10
    until "$@"; do
        n=$((n+1))
        if [ $n -ge $max ]; then
            echo "FAILED after $max attempts: $*" >&2
            return 1
        fi
        echo "RETRY $n/$max in ${delay}s: $*"
        sleep $delay
    done
}

# ---------------------------------------------------------------------------
# PATH A: bench already exists (normal restart). Just start it.
# ---------------------------------------------------------------------------
if [ -d "/home/frappe/frappe-bench/apps/frappe" ]; then
    echo "Bench exists — syncing workspace app and starting."
    cd frappe-bench
    if [ -d "/workspace/crm_coldcall" ]; then
        rm -rf apps/crm_coldcall
        cp -r /workspace/crm_coldcall apps/crm_coldcall
        ./env/bin/pip install -q -e apps/crm_coldcall || true
        touch sites/apps.txt
        grep -qxF crm_coldcall sites/apps.txt || echo crm_coldcall >> sites/apps.txt
        bench --site crm.localhost migrate || true
        bench --site crm.localhost install-app crm_coldcall || true
    fi
    echo crm.localhost > sites/currentsite.txt 2>/dev/null || true
    bench start
    exit 0
fi

# ---------------------------------------------------------------------------
# PATH B: fresh bench
# ---------------------------------------------------------------------------
echo "Creating new bench..."
rm -rf /home/frappe/frappe-bench
retry bench init --skip-redis-config-generation frappe-bench --version version-15

cd frappe-bench
bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis:6379
bench set-redis-queue-host redis://redis:6379
bench set-redis-socketio-host redis://redis:6379
sed -i '/redis/d' ./Procfile
sed -i '/watch/d' ./Procfile

retry bench get-app whatsapp
retry bench get-app crm --branch main
if [ -d "/workspace/crm_coldcall" ]; then
    rm -rf apps/crm_coldcall
    cp -r /workspace/crm_coldcall apps/crm_coldcall
    ./env/bin/pip install -q -e apps/crm_coldcall || true
    ./env/bin/pip install -q pypdf || true
    # `bench get-app` chokes on local paths in this bench version, so register
    # the app manually in sites/apps.txt (newline-safe append).
    touch sites/apps.txt
    grep -qxF crm_coldcall sites/apps.txt || echo crm_coldcall >> sites/apps.txt
fi

echo "Waiting for MariaDB..."
for i in $(seq 1 60); do
    mysql --protocol=tcp --host=mariadb --user=root --password=123 -e "SELECT 1;" 2>/dev/null && break
    sleep 2
done

# Belt & braces: force non-SSL on server side too.
mysql --protocol=tcp --host=mariadb --user=root --password=123 -e "SET GLOBAL require_secure_transport=OFF;" 2>/dev/null || true
mysql --protocol=tcp --host=mariadb --user=root --password=123 -e "ALTER USER 'root'@'%' REQUIRE NONE; FLUSH PRIVILEGES;" 2>/dev/null || true

bench new-site crm.localhost \
    --force \
    --mariadb-root-password 123 \
    --admin-password admin \
    --mariadb-user-host-login-scope='%' \
    --db-host mariadb

bench --site crm.localhost install-app crm
if [ -d "apps/crm_coldcall" ]; then
    # Sync the app's DocTypes FIRST: its after_install hook creates custom
    # fields that link to `Cold Call Service Item`, which only exists after a
    # migrate. Installing before that raises WrongOptionsDoctypeLinkError.
    bench --site crm.localhost migrate || true
    bench --site crm.localhost install-app crm_coldcall || true
fi
bench --site crm.localhost set-config developer_mode 1
bench --site crm.localhost set-config mute_emails 1
bench --site crm.localhost set-config server_script_enabled 1
bench use crm.localhost
echo crm.localhost > sites/currentsite.txt

bench start
