# Testnet strategy review and structural backtest — 2026-07-14

## Verdict

`FAIL_RESEARCH_GATE` for production qualification. This historical verdict originally kept the
campaign observation-only. Owner direction later superseded that operational restriction for
explicitly labelled Testnet experiment sampling only; ADR 0007 does not alter this failed result or
unlock production.

## Exact forward result

The running five-symbol baseline produced 679 observations before this review. None was eligible,
none was execution-ready and none had both 1m and 5m Price Action in the required long state. Only
46 observations contained recent aggressive trades, confirming that ten-second REST polling is
not a sufficient replacement for the causal real-time Order Flow stream.

After the review, the campaign was upgraded to a persistent five-symbol `aggTrade` WebSocket and a
five-second normal-quantity window. Its first two complete deployed rounds had non-zero aggressive
flow in all 10 symbol observations. This fixes the collection defect but does not retroactively
change the failed backtest or authorize an entry.

## Conservative structural replay

The reproducible research runner used the latest approximately 25 hours of Testnet history for
SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT and ADAUSDT. It applied the documented T1 trend-pullback proxy,
required a confirmed structural stop and structural target, used the actual 0.0400% Testnet taker
fee per side, applied 1 bps adverse entry and exit slippage and assumed 10 USDT notional.

No elapsed-time exit exists. Positions close only at their structural stop or target, and a
same-bar ambiguity is resolved stop-first.

| Metric | Result |
|---|---:|
| Closed trades | 2 |
| Winning trades | 0 |
| Win rate | 0% |
| Net result at 10 USDT notional | -0.0389499593 USDT |
| SOLUSDT | -0.0243478760 USDT, structure stop |
| XRPUSDT | -0.0146020833 USDT, structure stop |

The sample is too small for statistical approval and its observed result is negative. Historical
klines also lack the required causal L2 order book and normal-quantity aggregate-trade stream, so
the proxy cannot qualify production even if its result later becomes positive.

Machine-readable evidence is
`/var/lib/ai-quant/evidence/testnet/backtest/current/structural-review.json`. New forward
observations now include `mid_price` and `microprice`, allowing future structure-only markout and
exit analysis without inventing old prices.

## Runtime decision

At the time of this review, `aiq-testnet-campaign.service` was `OBSERVATION_ONLY`, its
order-submission path was absent and the account was flat. A Chinese Telegram notification with the
result was delivered. ADR 0007 subsequently added a separately classified
`UNVALIDATED_TESTNET_EXPERIMENT` execution path to collect the missing forward samples; production
entry remains fail-closed.
