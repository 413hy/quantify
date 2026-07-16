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
  if [[ -f "$state_file" ]] && jq -e '
    (.status == "RUNNING" or .status == "STOPPED")
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
  if [[ -f "$state_file" ]] \
    && systemctl is-active --quiet "$service" \
    && jq -e '
      .status == "RUNNING"
      and .strategy == "TESTNET_EXPERIMENT_OF_PA_V5_6"
      and .symbols == ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
      and .limits.maximum_parallel_positions == 5
      and .limits.maximum_candidates_per_round == 5
      and .limits.trade_cooldown_seconds == 0
      and .limits.market_episode_entry_limit_enabled == false
      and .limits.duplicate_signal_suppression_enabled == true
      and .limits.evaluation_interval_seconds == 60
      and .limits.minimum_directional_forecast_bps == "2.00"
      and .limits.impulse_minimum_directional_forecast_bps == "0.10"
      and .limits.continuation_minimum_directional_forecast_bps == "2.00"
      and .limits.structure_substitute_minimum_directional_forecast_bps == "3.00"
      and .limits.minimum_target_feasibility_rate_15m == "0.20"
      and .limits.impulse_minimum_target_feasibility_rate_15m == "0.02"
      and .limits.minimum_net_reward_risk_ratio == "0.50"
      and .limits.impulse_minimum_net_reward_risk_ratio == "0.15"
      and .limits.same_direction_scale_enabled == false
      and .limits.automatic_reversal_entry_enabled == true
      and .limits.activity_filter_enabled == false
      and .limits.impulse_activity_filter_enabled == false
      and .limits.impulse_minimum_activity_ratio == "0.10"
      and .limits.impulse_maximum_activity_ratio == "10.00"
      and .limits.impulse_maximum_momentum_bps == "8.00"
      and .limits.impulse_lookback_rounds == 4
      and .limits.impulse_minimum_breadth_count == 3
      and .limits.sustained_lookback_rounds == 5
      and .limits.pullback_minimum_bps == "3.00"
      and .limits.pullback_resumption_bps == "0.50"
      and .limits.pullback_maximum_bps == "40.00"
      and .limits.pullback_setup_maximum_rounds == 10
      and .limits.signal_evidence_window_rounds == 2
      and .limits.continuation_minimum_breadth_count == 4
      and .limits.continuation_confirmation_rounds == 1
      and .limits.continuation_minimum_momentum_bps == "4.00"
      and .limits.continuation_maximum_momentum_bps == "15.00"
      and .limits.position_opposition_confirmation_rounds == 1
      and .submitted_trade_count == 0
      and .trade_count == 0
    ' "$state_file" >/dev/null; then
    exit 0
  fi
  sleep 2
done

exit 1
