#!/bin/sh
set -u

export TZ="${TZ:-Asia/Bangkok}"

next_run_delay() {
  python -c '
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

zone = ZoneInfo("'"${TZ}"'")
now = datetime.now(zone)
candidates = [
    now.replace(hour=hour, minute=5, second=0, microsecond=0)
    for hour in range(8, 24)
]
target = next((value for value in candidates if value > now), None)
if target is None:
    tomorrow = now + timedelta(days=1)
    target = tomorrow.replace(hour=8, minute=5, second=0, microsecond=0)
print(max(1, int((target - now).total_seconds())))
'
}

while true; do
  delay="$(next_run_delay)"
  echo "Next Shopee sync in ${delay} seconds"
  sleep "$delay"

  if node /app/klean_pod_checker/download_shopee_report.js; then
    python -m klean_pod_checker.shopee_sales_sync || \
      echo "Shopee report import failed; the scheduler will retry next hour" >&2
  else
    echo "Shopee report download failed; the scheduler will retry next hour" >&2
  fi
done
