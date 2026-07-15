#!/usr/bin/env bash
set -euo pipefail

state_file=/var/lib/ai-quant/evidence/testnet/campaign/current/state.json
evidence_directory=/var/lib/ai-quant/evidence/testnet/campaign/current
archive_directory=/var/lib/ai-quant/evidence/testnet/campaign/archive
service=aiq-testnet-campaign.service
lock_file=/run/ai-quant/testnet-campaign-cutover.lock

exec 9>"$lock_file"
flock -n 9

while true; do
  if jq -e '
    .status == "RUNNING"
    and (.active_symbols | type == "array")
    and (.active_symbols | length == 0)
    and (.pending_entry_symbols | type == "array")
    and (.pending_entry_symbols | length == 0)
  ' "$state_file" >/dev/null; then
    break
  fi
  sleep 1
done

strategy=$(jq -r '.strategy // "unknown"' "$state_file" | tr '[:upper:]' '[:lower:]')
archive_name="$(date -u +%Y%m%dT%H%M%SZ)-${strategy}-final"

systemctl stop "$service"
install -d -m 0700 "$archive_directory"
mv "$evidence_directory" "$archive_directory/$archive_name"
install -d -m 0700 "$evidence_directory"
systemctl start "$service"

for _attempt in $(seq 1 30); do
  if systemctl is-active --quiet "$service" \
    && jq -e '
      .status == "RUNNING"
      and .strategy == "TESTNET_EXPERIMENT_OF_PA_V4_11"
      and .symbols == ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
      and .limits.maximum_candidates_per_round == 5
      and .submitted_trade_count == 0
      and .trade_count == 0
    ' "$state_file" >/dev/null; then
    exit 0
  fi
  sleep 2
done

exit 1
