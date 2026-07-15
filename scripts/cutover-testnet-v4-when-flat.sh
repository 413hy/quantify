#!/usr/bin/env bash
set -euo pipefail

state_file=/var/lib/ai-quant/evidence/testnet/campaign/current/state.json
service=aiq-testnet-campaign.service
lock_file=/run/ai-quant/testnet-campaign-cutover.lock

exec 9>"$lock_file"
flock -n 9

while true; do
  if jq -e '
    .status == "RUNNING"
    and (.active_symbols | type == "array")
    and (.active_symbols | length == 0)
  ' "$state_file" >/dev/null; then
    break
  fi
  sleep 5
done

systemctl restart "$service"

for _attempt in $(seq 1 30); do
  if systemctl is-active --quiet "$service" \
    && jq -e '
      .status == "RUNNING"
      and .strategy == "TESTNET_EXPERIMENT_OF_PA_V4_4"
      and .symbols == ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
    ' "$state_file" >/dev/null; then
    exit 0
  fi
  sleep 2
done

exit 1
