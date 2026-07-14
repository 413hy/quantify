#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="aiq-m0-locked-$$"
TMP="$(mktemp -d /tmp/aiq-m0-locked.XXXXXX)"

cleanup() {
  docker rm -f "$RUN_ID" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

chown 65532:65532 "$TMP"
chmod 0770 "$TMP"
docker run --rm -d --name "$RUN_ID" \
  --network none \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$TMP,dst=/run/ai-quant-rate" \
  -e AIQ_SOCKET_PATH=/run/ai-quant-rate/rate.sock \
  aiq-app:m0 >/dev/null

for _ in $(seq 1 50); do
  test -S "$TMP/rate.sock" && break
  sleep 0.1
done
test -S "$TMP/rate.sock"

test "$(docker inspect "$RUN_ID" --format '{{.Config.User}}')" = "65532:65532"
cd "$ROOT"
uv run python scripts/probe_locked_socket.py "$TMP/rate.sock"
