#!/usr/bin/env bash
# Screenshot app pages in BOTH themes, signed in as a local test account.
# LOCAL ONLY (test logins come from scripts/seed_test_logins.py, password 12345).
#
#   .claude/skills/apexmed-ui/scripts/shoot.sh OUT_DIR [/path ...]
#
# Env: UI_BASE (default http://127.0.0.1:8811; `next dev` = http://127.0.0.1:3000),
#      UI_USER/UI_PASS (1000/12345 = ADMIN), UI_VIEWPORT (1440,900; phone 390,844),
#      UI_FULL=1 full-page, UI_WAIT ms before the shot (2500).
set -euo pipefail
BASE=${UI_BASE:-http://127.0.0.1:8811}
OUT=${1:?usage: shoot.sh OUT_DIR [/path ...]}; shift
PAGES=("$@")
[ ${#PAGES[@]} -eq 0 ] && PAGES=(/login /dashboard /tasks /account /admin/departments /resources)
PW=${UI_PLAYWRIGHT:-playwright@1.57.0}
mkdir -p "$OUT"
token=$(curl -s -D - -o /dev/null -X POST "$BASE/api/auth/password-login" \
  -H 'content-type: application/json' \
  -d "{\"username\":\"${UI_USER:-1000}\",\"password\":\"${UI_PASS:-12345}\"}" |
  sed -nE 's/^[Ss]et-[Cc]ookie: meobot_web_session=([^;]+).*/\1/p' | tr -d '\r')
[ -n "$token" ] || { echo "login failed at $BASE" >&2; exit 1; }
cookie() { printf '{"name":"%s","value":"%s","domain":"127.0.0.1","path":"/","expires":-1,"httpOnly":true,"secure":false,"sameSite":"Lax"}' "$1" "$2"; }
for theme in light dark; do
  printf '{"cookies":[%s,%s],"origins":[]}' "$(cookie meobot_web_session "$token")" "$(cookie meobot_theme "$theme")" >"$OUT/.in-$theme.json"
  printf '{"cookies":[%s],"origins":[]}' "$(cookie meobot_theme "$theme")" >"$OUT/.out-$theme.json"
  for page in "${PAGES[@]}"; do
    name=$(printf '%s' "$page" | sed 's#^/##; s#[/?=&]#_#g'); name=${name:-root}
    state="$OUT/.in-$theme.json"; case $page in /login*) state="$OUT/.out-$theme.json";; esac
    npx -y "$PW" screenshot --load-storage="$state" --color-scheme="$theme" \
      --viewport-size="${UI_VIEWPORT:-1440,900}" --wait-for-timeout="${UI_WAIT:-2500}" \
      ${UI_FULL:+--full-page} "$BASE$page" "$OUT/$name-$theme.png" >/dev/null
    echo "$OUT/$name-$theme.png"
  done
done
rm -f "$OUT"/.in-*.json "$OUT"/.out-*.json
