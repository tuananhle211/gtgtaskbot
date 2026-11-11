#!/bin/sh
# Restore check - plan docs/ops/SCALING_PLAN.md C2.
#
# Restores the newest daily dump into a SCRATCH database inside the postgres
# container, compares a few row counts with the live database, then drops the
# scratch database. The live database is only read. Run on the server:
#   sh /root/gtgtask/deploy/backup/restore-check.sh [dump file]
# Needs free disk about twice the database (193 MB on 11/11/2026).
set -eu
cd /root/gtgtask

F=${1:-$(ls -1t /root/db-backups/daily/meobot-*.dump | head -n 1)}
SCRATCH="restore_check_$(date +%Y%m%d%H%M)"
echo "dump: $F"
echo "scratch database: $SCRATCH"

psql_live() {
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"$1\""
}
psql_scratch() {
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d $SCRATCH -Atc \"$1\""
}
cleanup() {
  docker compose exec -T postgres sh -c "dropdb -U \"\$POSTGRES_USER\" --if-exists $SCRATCH" || true
}
trap cleanup EXIT

docker compose exec -T postgres sh -c "createdb -U \"\$POSTGRES_USER\" $SCRATCH"
docker compose exec -T postgres sh -c "pg_restore -U \"\$POSTGRES_USER\" -d $SCRATCH --no-owner --exit-on-error" <"$F"

echo "table           dump    live (live may be ahead: the dump is from 02:30)"
for table in users orders tasks hr_profiles hr_attendance_days cskh_messages audit_logs; do
  dumped=$(psql_scratch "select count(*) from $table")
  live=$(psql_live "select count(*) from $table")
  printf '%-15s %-7s %s\n' "$table" "$dumped" "$live"
done
echo "alembic head in the dump: $(psql_scratch 'select version_num from alembic_version')"
echo "restore ok"
