#!/bin/sh
# Daily dump of the production DB (meobot) - plan docs/ops/SCALING_PLAN.md C2.
#
# Installed as /root/bin/db-backup-daily.sh and run by /etc/cron.d/meobot-maintenance
# at 02:30 (VN). What it does:
#   1. pg_dump -Fc, checked with pg_restore -l, kept in /root/db-backups/daily (3 newest);
#   2. copied to Google Drive (rclone remote "gdrive", scope drive.file);
#   3. on Drive keeps the 14 newest dumps plus one per Sunday for the 8 Sundays
#      before them (about 2 weeks day by day + 2 months week by week, ~1.5 GB);
#   4. reports the run to "Tác vụ định kỳ" (job host.db_backup): a failure at any
#      step alerts the OWNER like any other job.
# Not encrypted (user's decision 11/11/2026, see the plan §6).
set -eu
umask 077
cd /root/gtgtask

D=/root/db-backups/daily
R=gdrive:APM-backups/prod-db
KEEP_LOCAL=3
KEEP_DAILY=14
KEEP_WEEKLY=8
STARTED=$(date +%s)
STEP="bắt đầu"

report() { # $1 = ok | failed, $2 = message
  docker compose exec -T api python -m meobot.application.cron.host_job \
    --key host.db_backup --status "$1" --started "$STARTED" --message "$2" >/dev/null 2>&1 ||
    echo "$(date "+%F %T") could not report to Tác vụ định kỳ"
}
on_exit() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "$(date "+%F %T") FAILED at: $STEP"
    report failed "Sao lưu lỗi ở bước: $STEP"
  fi
}
trap on_exit EXIT

mkdir -p "$D"
F="$D/meobot-$(date +%F-%H%M).dump"

STEP="pg_dump"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$F.part"
STEP="kiểm tra bản dump (pg_restore -l)"
docker compose exec -T postgres pg_restore -l <"$F.part" >/dev/null
mv "$F.part" "$F"
SIZE=$(du -h "$F" | cut -f1)

STEP="dọn bản cũ trên máy chủ"
ls -1t "$D"/meobot-*.dump | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f
find "$D" -name "*.part" -mmin +120 -delete
echo "$(date "+%F %T") ok $F $SIZE"

STEP="chép lên Google Drive"
rclone copy "$F" "$R"

STEP="dọn bản cũ trên Google Drive"
# Names sort by date: meobot-YYYY-MM-DD-HHMM.dump. Newest first.
ALL=$(rclone lsf "$R" --include "meobot-*.dump" | sort -r)
[ -n "$ALL" ] || { echo "Drive listing is empty, nothing deleted"; exit 1; }
DAILY=$(printf '%s\n' "$ALL" | head -n "$KEEP_DAILY")
# Older than the daily window: the first dump of each Sunday, 8 Sundays.
WEEKLY=$(printf '%s\n' "$ALL" | tail -n +$((KEEP_DAILY + 1)) | while read -r name; do
  day=$(printf '%s' "$name" | cut -c8-17)
  [ "$(date -d "$day" +%u 2>/dev/null)" = "7" ] && printf '%s %s\n' "$day" "$name"
done | sort -r -u -k1,1 | head -n "$KEEP_WEEKLY" | cut -d' ' -f2)
KEEP=$(printf '%s\n%s\n' "$DAILY" "$WEEKLY")
printf '%s\n' "$ALL" | while read -r name; do
  [ -n "$name" ] || continue
  printf '%s\n' "$KEEP" | grep -qxF "$name" && continue
  rclone deletefile "$R/$name" --drive-use-trash=false
done
COUNT=$(rclone lsf "$R" --include "meobot-*.dump" | wc -l | tr -d ' ')
echo "$(date "+%F %T") drive ok $COUNT files"

STEP="báo kết quả"
report ok "Bản $(basename "$F") ($SIZE); trên Google Drive còn $COUNT bản."
