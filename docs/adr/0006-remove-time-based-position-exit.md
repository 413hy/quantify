# ADR 0006: Remove time-based position exits

- Status: accepted by owner
- Date: 2026-07-14

## Decision

The owner explicitly overrides the earlier frozen 30-second-to-15-minute holding-time baseline.
No elapsed wall-clock duration may by itself close a position.

An open position may be reduced or closed only because of:

- kill switch, protection failure, account inconsistency or a hard risk breach;
- the exchange-native structural stop;
- Price Action structure invalidation;
- Order Flow exhaustion or reversal;
- the structural strategy target or approved partial take-profit logic;
- an explicit operator or reconciliation action.

When market data is temporarily unhealthy and exchange-native protection remains healthy, the
position is held. Elapsed time is not an exit signal, regardless of how long the position has been
open. Entry remains prohibited unless the strategy supplies a structural stop and target and the
required net-edge evidence passes.

## Consequences

The `maximum_holding_seconds` configuration and position field, the TradePlan holding horizon and
the standalone fixed-time Testnet micro-position runners are removed. Historical evidence from
earlier Testnet protocol samples remains an immutable record of what happened, but those runners
are retired and cannot be used to create new positions.
