#!/usr/bin/env bash
# brain-commit (chạy bằng user git, systemd timer 5 phút): đưa ghi chú agent viết qua brain-mcp
# (outbox) vào kho bằng một commit thường - chỉ 01-inbox-ai/ và 02-de-xuat/. Lỗi (vd đua push với
# người) thì để nguyên outbox, lần sau thử lại.
set -euo pipefail
OUT=/srv/brain/outbox
WORK=/srv/brain/work
REPO=/home/git/secondbrain.git

cd "$OUT"
mapfile -d '' FILES < <(find 01-inbox-ai 02-de-xuat -type f -name '*.md' -print0 2>/dev/null || true)
[ "${#FILES[@]}" -eq 0 ] && exit 0

[ -d "$WORK/.git" ] || git clone -q "$REPO" "$WORK"
cd "$WORK"
git fetch -q origin
git checkout -q -B main origin/main
for f in "${FILES[@]}"; do
  case "$f" in 01-inbox-ai/*|02-de-xuat/*) ;; *) continue ;; esac
  mkdir -p "$(dirname "$f")"
  cp "$OUT/$f" "$f"
  git add -- "$f"
done
git -c user.name="brain-mcp" -c user.email="brain-mcp@localhost" \
  commit -q -m "AI: ${#FILES[@]} ghi chú nháp / đề xuất từ agent (brain-mcp)"
git push -q origin main
for f in "${FILES[@]}"; do rm -f -- "$OUT/$f"; done
echo "đã đưa ${#FILES[@]} tệp vào kho"
