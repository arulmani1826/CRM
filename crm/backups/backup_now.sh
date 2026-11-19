#!/bin/bash
# Host-safe backup of all site databases. Run from INSIDE frappe container.
# Usage: bash /workspace/backups/backup_now.sh [tag]
set -e
TAG="${1:-manual}"
mkdir -p /workspace/backups
DBS=$(mysql -h mariadb -uroot -p123 -N -e "SHOW DATABASES;" 2>/dev/null | grep -v -E "^(information_schema|mysql|performance_schema|sys)$" || true)
if [ -z "$DBS" ]; then
  echo "no site databases found"
  exit 0
fi
for db in $DBS; do
  out="/workspace/backups/${db}-${TAG}-$(date +%Y%m%d-%H%M%S).sql.gz"
  mysqldump -h mariadb -uroot -p123 --single-transaction --routines "$db" 2>/dev/null | gzip > "$out"
  echo "backed up $db -> $out"
done
ls -l /workspace/backups
