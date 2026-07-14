#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="aiq-m0-migrations-$$"
TMP="$(mktemp -d /tmp/aiq-m0-migrations.XXXXXX)"
COMPOSE=(docker compose -p "$RUN_ID" -f "$ROOT/deploy/compose.test.yaml")

cleanup() {
  TEST_BUSINESS_DB_PASSWORD_FILE="$TMP/business" \
  TEST_HOST_DB_PASSWORD_FILE="$TMP/host" \
    "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

umask 077
openssl rand -hex 24 > "$TMP/business"
openssl rand -hex 24 > "$TMP/host"
BUSINESS_PASSWORD="$(<"$TMP/business")"
HOST_PASSWORD="$(<"$TMP/host")"
export TEST_BUSINESS_DB_PASSWORD_FILE="$TMP/business"
export TEST_HOST_DB_PASSWORD_FILE="$TMP/host"

"${COMPOSE[@]}" up -d --wait business-postgres host-postgres redis

printf 'postgresql+psycopg://aiq_business_test:%s@business-postgres:5432/aiq_business_test' \
  "$BUSINESS_PASSWORD" > "$TMP/business-dsn"
printf 'postgresql+psycopg://aiq_host_control_test:%s@host-postgres:5432/aiq_host_rate_control_test' \
  "$HOST_PASSWORD" > "$TMP/host-dsn"
chown 65532:65532 "$TMP/business-dsn" "$TMP/host-dsn"
chmod 0400 "$TMP/business-dsn" "$TMP/host-dsn"

cd "$ROOT"
run_business_migration() {
  docker run --rm --network "${RUN_ID}_test_db_net" \
    --mount "type=bind,src=$TMP/business-dsn,dst=/run/secrets/business-dsn,readonly" \
    -e AIQ_BUSINESS_DATABASE_URL_FILE=/run/secrets/business-dsn \
    aiq-app:m0 alembic -c migrations/business/alembic.ini "$@"
}
run_host_migration() {
  docker run --rm --network "${RUN_ID}_test_db_net" \
    --mount "type=bind,src=$TMP/host-dsn,dst=/run/secrets/host-dsn,readonly" \
    -e AIQ_HOST_CONTROL_DATABASE_URL_FILE=/run/secrets/host-dsn \
    aiq-app:m0 alembic -c migrations/host_control/alembic.ini "$@"
}

run_business_migration upgrade head
run_host_migration upgrade head
run_business_migration downgrade base
run_host_migration downgrade base
run_business_migration upgrade head
run_host_migration upgrade head

printf 'migration round-trip PASS\n'

docker exec "${RUN_ID}-business-postgres-1" psql -U aiq_business_test -d aiq_business_test -Atc \
  "SELECT state || ':' || new_entries_allowed FROM control.runtime_state" | grep -qx 'RISK_LOCKED:false'
docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test -d aiq_host_rate_control_test -Atc \
  "SELECT epoch || ':' || allocator_instance_id FROM rate_control.fencing_state" | grep -qx '1:UNINITIALIZED'
docker exec "${RUN_ID}-business-postgres-1" psql -U aiq_business_test -d aiq_business_test -Atc \
  "SELECT extversion FROM pg_extension WHERE extname='timescaledb'" | grep -Eq '^2\.'
docker exec "${RUN_ID}-redis-1" redis-cli ping | grep -qx PONG

printf 'database invariants PASS\n'

HASH_A="$(printf 'a%.0s' {1..64})"
HASH_B="$(printf 'b%.0s' {1..64})"
HASH_C="$(printf 'c%.0s' {1..64})"
HASH_D="$(printf 'd%.0s' {1..64})"
HASH_E="$(printf 'e%.0s' {1..64})"
HASH_F="$(printf 'f%.0s' {1..64})"
docker exec -i "${RUN_ID}-host-postgres-1" psql -v ON_ERROR_STOP=1 \
  -U aiq_host_control_test -d aiq_host_rate_control_test <<SQL >/dev/null
INSERT INTO rate_control.endpoint_runtime_policies(
  endpoint_authority,endpoint_id,endpoint_catalog_hash,policy_payload_hash,status,
  allowed_callers,derived_operation_class,cost_vector,valid_from,valid_until
) VALUES (
  'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','$HASH_B','SIGNED_RUNTIME',
  ARRAY['execution-service'],'HOST_RATE_CONTROL',
  '[{"scope_key_hash":"$HASH_C","rate_limit_type":"REQUEST_WEIGHT","interval_name":"MINUTE_1","cost":1,"ceiling_units":10}]',
  now()-interval '1 minute',now()+interval '1 minute'
);
INSERT INTO rate_control.rate_windows(
  endpoint_authority,scope_key_hash,rate_limit_type,interval_name,window_start,
  window_end,effective_used,observed_max,hard_limit,limit_source_hash
) VALUES (
  'BINANCE_PRODUCTION_FAPI','$HASH_C','REQUEST_WEIGHT','MINUTE_1',
  now()-interval '1 minute',now()+interval '1 minute',0,0,10,'$HASH_D'
);
INSERT INTO rate_control.capability_nonces(nonce,payload_hash,state,expires_at)
VALUES ('nonce-1','$HASH_E','RESERVED',now()+interval '1 minute');
INSERT INTO rate_control.permits(
  permit_id,request_key,subject_caller_service,subject_caller_instance_id,
  environment,endpoint_authority,endpoint_id,endpoint_catalog_hash,derived_operation_class,
  canonical_request_hash,parameter_hash,wire_bytes_hash,
  operation_facts_hash,capability_payload_hash,gateway_request_document_hash,
  capability_nonce,fencing_epoch,
  state,reserved_at,expires_at
) VALUES (
  'permit-1','request-1','execution-service','execution-1','production',
  'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','HOST_RATE_CONTROL',
  '$HASH_A','$HASH_B','$HASH_C','$HASH_D','$HASH_E','$HASH_F','nonce-1',1,
  'RESERVED',now(),now()+interval '1 minute'
);
SQL

RESERVE_FIRST="$(docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code||':'||permit_id FROM rate_control.reserve_permit(
    'permit-reserve-1','request-reserve-1','execution-service','execution-1','production',
    'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','$HASH_A','$HASH_B','$HASH_C',
    '$HASH_D','$HASH_E','$HASH_F','nonce-reserve-1',1,now()+interval '4 seconds')")"
RESERVE_RETRY="$(docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code||':'||permit_id FROM rate_control.reserve_permit(
    'permit-reserve-ignored','request-reserve-1','execution-service','execution-1','production',
    'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','$HASH_A','$HASH_B','$HASH_C',
    '$HASH_D','$HASH_E','$HASH_F','nonce-reserve-1',1,now()+interval '4 seconds')")"
RESERVE_REPLAY="$(docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code FROM rate_control.reserve_permit(
    'permit-reserve-2','request-reserve-2','execution-service','execution-1','production',
    'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','$HASH_A','$HASH_B','$HASH_C',
    '$HASH_D','$HASH_E','$HASH_F','nonce-reserve-1',1,now()+interval '4 seconds')")"
printf 'reserve decisions first=%s retry=%s replay=%s\n' \
  "$RESERVE_FIRST" "$RESERVE_RETRY" "$RESERVE_REPLAY"
test "$RESERVE_FIRST" = 'GRANTED:RATE_GRANTED:permit-reserve-1'
test "$RESERVE_RETRY" = 'GRANTED:RATE_GRANTED:permit-reserve-1'
test "$RESERVE_REPLAY" = 'DENIED:RATE_CAPABILITY_REPLAYED'
docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "SELECT effective_used FROM rate_control.rate_windows WHERE scope_key_hash='$HASH_C'" \
  | grep -qx '1'
printf 'atomic reservation and idempotency PASS\n'

RESERVE_CALLER_DENIED="$(docker exec "${RUN_ID}-host-postgres-1" psql \
  -U aiq_host_control_test -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code FROM rate_control.reserve_permit(
    'permit-denied-1','request-denied-1','trading-engine','trading-1','production',
    'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','$HASH_A','$HASH_B','$HASH_C',
    '$HASH_D','$HASH_E','$HASH_F','nonce-denied-1',1,now()+interval '4 seconds')")"
RESERVE_FENCING_DENIED="$(docker exec "${RUN_ID}-host-postgres-1" psql \
  -U aiq_host_control_test -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code FROM rate_control.reserve_permit(
    'permit-denied-2','request-denied-2','execution-service','execution-1','production',
    'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','$HASH_A','$HASH_B','$HASH_C',
    '$HASH_D','$HASH_E','$HASH_F','nonce-denied-2',2,now()+interval '4 seconds')")"
RESERVE_CATALOG_DENIED="$(docker exec "${RUN_ID}-host-postgres-1" psql \
  -U aiq_host_control_test -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code FROM rate_control.reserve_permit(
    'permit-denied-3','request-denied-3','execution-service','execution-1','production',
    'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_F','$HASH_A','$HASH_B','$HASH_C',
    '$HASH_D','$HASH_E','$HASH_F','nonce-denied-3',1,now()+interval '4 seconds')")"
test "$RESERVE_CALLER_DENIED" = 'DENIED:RATE_CALLER_NOT_ALLOWED'
test "$RESERVE_FENCING_DENIED" = 'DENIED:RATE_FENCING_STALE'
test "$RESERVE_CATALOG_DENIED" = 'DENIED:RATE_ENDPOINT_UNKNOWN'

docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "UPDATE rate_control.rate_windows SET blocked_until=now()+interval '1 minute' WHERE scope_key_hash='$HASH_C'" \
  >/dev/null
RESERVE_BLOCKED="$(docker exec "${RUN_ID}-host-postgres-1" psql \
  -U aiq_host_control_test -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code FROM rate_control.reserve_permit(
    'permit-denied-4','request-denied-4','execution-service','execution-1','production',
    'BINANCE_PRODUCTION_FAPI','REST_QUERY_TIME','$HASH_A','$HASH_A','$HASH_B','$HASH_C',
    '$HASH_D','$HASH_E','$HASH_F','nonce-denied-4',1,now()+interval '4 seconds')")"
test "$RESERVE_BLOCKED" = 'DENIED:RATE_SCOPE_BLOCKED'
printf 'reservation denial gates caller=%s fencing=%s catalog=%s blocked=%s\n' \
  "$RESERVE_CALLER_DENIED" "$RESERVE_FENCING_DENIED" \
  "$RESERVE_CATALOG_DENIED" "$RESERVE_BLOCKED"

FIRST_CONSUME="$(docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code FROM rate_control.consume_permit(
    'permit-1','$HASH_A','$HASH_B','$HASH_C','$HASH_D','$HASH_E','$HASH_F',1,'gateway-1')")"
SECOND_CONSUME="$(docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "SELECT decision||':'||reason_code FROM rate_control.consume_permit(
    'permit-1','$HASH_A','$HASH_B','$HASH_C','$HASH_D','$HASH_E','$HASH_F',1,'gateway-1')")"
printf 'permit consume decisions first=%s second=%s\n' "$FIRST_CONSUME" "$SECOND_CONSUME"
test "$FIRST_CONSUME" = 'CONSUME_GRANTED:RATE_PERMIT_CONSUMED'
test "$SECOND_CONSUME" = 'CONSUME_DENIED:PERMIT_NOT_RESERVED'
docker exec "${RUN_ID}-host-postgres-1" psql -U aiq_host_control_test \
  -d aiq_host_rate_control_test -Atc \
  "SELECT p.state||':'||n.state FROM rate_control.permits p JOIN rate_control.capability_nonces n ON n.nonce=p.capability_nonce WHERE p.permit_id='permit-1'" \
  | grep -qx 'CONSUMED:CONSUMED'

printf 'permit and nonce terminal states PASS\n'

printf 'migration integration PASS project=%s\n' "$RUN_ID"
