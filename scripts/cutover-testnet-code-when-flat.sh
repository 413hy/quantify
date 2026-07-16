#!/usr/bin/env bash
set -euo pipefail

state_file=/var/lib/ai-quant/evidence/testnet/campaign/current/state.json
service=aiq-testnet-campaign.service
lock_file=/run/ai-quant/testnet-code-cutover.lock

exec 9>"$lock_file"
flock -n 9

while true; do
  if [[ -f "$state_file" ]] \
    && systemctl is-active --quiet "$service" \
    && jq -e '
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

previous_pid=$(systemctl show "$service" -p MainPID --value)
systemctl stop "$service"
# A graceful stop deliberately records STOPPED.  For a same-version, flat code
# cutover preserve the campaign counters and evidence stream by restoring the
# resumable marker before starting the new process.
state_tmp="${state_file}.cutover.$$"
jq '.status = "RUNNING"' "$state_file" >"$state_tmp"
chmod 0600 "$state_tmp"
mv "$state_tmp" "$state_file"
systemctl start "$service"

for _attempt in $(seq 1 30); do
  current_pid=$(systemctl show "$service" -p MainPID --value)
  if [[ -f "$state_file" ]] \
    && systemctl is-active --quiet "$service" \
    && [[ "$current_pid" != "0" ]] \
    && [[ "$current_pid" != "$previous_pid" ]] \
    && jq -e '
      .status == "RUNNING"
      and .strategy == "TESTNET_EXPERIMENT_OF_PA_V5_6"
      and .limits.execution_forecast_threshold_source == "CONFIRMED_PLAN"
      and .limits.evaluation_interval_seconds == 60
      and .limits.minimum_directional_forecast_bps == "2.00"
      and .limits.impulse_minimum_directional_forecast_bps == "0.10"
      and .limits.continuation_minimum_directional_forecast_bps == "2.00"
      and .limits.structure_substitute_minimum_directional_forecast_bps == "3.00"
      and .limits.maximum_parallel_positions == 5
      and .limits.maximum_candidates_per_round == 5
      and .limits.trade_cooldown_seconds == 0
      and .limits.market_episode_entry_limit_enabled == false
      and .limits.duplicate_signal_suppression_enabled == true
      and .limits.automatic_reversal_entry_enabled == true
      and .limits.activity_filter_enabled == false
      and .limits.impulse_minimum_breadth_count == 3
      and .limits.pullback_minimum_bps == "3.00"
      and .limits.pullback_resumption_bps == "0.50"
      and .limits.signal_evidence_window_rounds == 2
      and .limits.continuation_confirmation_rounds == 1
      and .limits.position_opposition_confirmation_rounds == 1
    ' "$state_file" >/dev/null; then
    exit 0
  fi
  sleep 2
done

exit 1
