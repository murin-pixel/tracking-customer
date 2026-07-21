#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:1}"
profile="${SHOPEE_PROFILE:-/home/milk/kleanandkare-shopee/session}"

mkdir -p "$profile"
rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}"
/usr/bin/Xvfb "$DISPLAY" -screen 0 1440x1000x24 -nolisten tcp -ac &
/bin/sleep 1
/usr/bin/openbox >/dev/null 2>&1 &

chromium_path="$(/usr/bin/node -e 'process.stdout.write(require("playwright").chromium.executablePath())')"
exec "$chromium_path" \
  --no-sandbox \
  --user-data-dir="$profile" \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --password-store=basic \
  https://seller.shopee.co.th/
