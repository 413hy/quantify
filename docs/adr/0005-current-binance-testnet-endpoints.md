# ADR 0005: Current Binance USD-M Futures Testnet endpoints

- Status: accepted
- Date: 2026-07-14
- Decision owner: account owner
- Scope: Testnet only

## Context

The immutable source package fixed the Testnet routed stream host to
`fstream.binancefuture.com`. Binance's current official USD-M Futures General Info instead names
`demo-fstream.binance.com` as the Testnet WebSocket base. The official WebSocket API General Info
continues to name `testnet.binancefuture.com/ws-fapi/v1` for the separate request/response API.

The owner explicitly authorized the implementation agent in the active session to configure the
current non-secret Testnet endpoints and continue Testnet validation. The immutable source package
remains byte-identical for provenance; this ADR and its runtime amendment supersede only the
affected Testnet destination selection.

## Decision

- REST: `https://demo-fapi.binance.com`
- routed market streams: `wss://demo-fstream.binance.com/{public,market,private}`
- optional WebSocket API: `wss://testnet.binancefuture.com/ws-fapi/v1`
- production destinations are unchanged and remain forbidden to Testnet callers
- redirects and any other destination remain fail-closed

The exact machine-readable values are in
`deploy/runtime/testnet-endpoints-20260714.json`. The gateway allowlist enforces the environment
and authority pairing rather than accepting aliases.

## Verification

On 2026-07-14 the Debian host successfully queried Testnet `/fapi/v1/time` and
`/fapi/v1/exchangeInfo`; the `/public` and `/market` routed hosts completed WebSocket HTTP 101
upgrades. Authenticated probes and the safe test-order endpoint are recorded separately so no
credential-derived response is committed.

## Consequences

The endpoint conflict in ADR 0001 is resolved for Testnet work. This decision does not authorize
production credentials, production orders, or a live-state transition.
