# Roadmap

## Completed in 2.0.0: Borrow, Borrowability and Delisting Settlement

- Added an annualized short borrow fee (`short_fee_rate`) in both period and daily NAV accounting.
- Added point-in-time `borrowable` gating for new short entries.
- Added symbol-level `delisting_settlement_price` to settle positions of securities that delist within the holding window.
- Added a multi-year chronological out-of-sample validation entrypoint (`scripts/oos_validation.py`).
- Lowered the Python floor to 3.10 with a `tomli` fallback for `tomllib`.

## Completed in 0.7.0: Deferred Exit and Daily NAV

- Carry positions that cannot exit at limit-down, limit-up or suspension instead of assuming a fill.
- Mark blocked positions daily and defer liquidation until the first directionally executable session.
- Prevent new target weights from spending capital still locked in carried positions.
- Replace sequential five-day period compounding with daily NAV and explicit order/position ledgers.
- Preserve every blocked order, retry date, fill assumption and cost in auditable evidence.

The ledger now carries and retries ordinary blocked exits. It still fails closed when an open security disappears after delisting without a verifiable settlement value.

## P0: Delisting Settlement Evidence (partially addressed in 2.0.0)

- Source exchange-backed delisting settlement or transfer-board values — now accepted as an optional `delisting_settlement_price` input column.
- Distinguish cash cancellation, transfer to another venue and unresolved beneficial ownership — still open.
- Replay settlement cash flows without retroactively changing prior NAV marks — settlement exits are recorded as explicit ledger attempts.
- Keep `error` as the default when no independently verifiable settlement value exists — unchanged.

## Completed in 0.5.0: Independent Factor Evidence

- Materialized a frozen Parquet table for every decision-date cross-section.
- Included symbol, trailing return, reversal score, group, entry/exit price and forward return.
- Bound the evidence table to JSON with row counts, schema version and SHA-256.
- Extended the validator to reconstruct deciles, Rank IC and coverage from that table.

The remaining work is to freeze and publish a provider-permitted multi-year evidence artifact, not to expand the contract further.

## P1: Data and Execution Evidence

- Validate the historical universe against an independent exchange source.
- Add delisting announcement/last-trading-day evidence visible before execution.
- Model borrow recalls and executable borrow queues for the short leg — fee and availability are now modeled; recalls and queues remain open.

## Completed in 0.6.0: Calendar and Daily Trading States

- Integrated and cached the official PandaData SH trading calendar.
- Mapped daily `trade_status`, historical ST names and adjusted limit prices.
- Added side-aware entry and exit constraints for limit-up and limit-down sessions.
- Bound trading states, limit prices and block reasons into JSON and full factor evidence.

## P1: Research Validation (partially addressed in 2.0.0)

- Run a multi-year full-market sample with rolling and chronological out-of-sample splits — `scripts/oos_validation.py` provides the chronological split entrypoint; run against a frozen multi-year panel or PandaData with credentials.
- Report cost, holding-period, decile-width and market-regime sensitivity — cost and short-fee are configurable; holding-period/decile sensitivity analysis remains open.
- Compare long-short research returns with executable long-only and index-hedged variants.

## P2: Distribution

- Add installed console entrypoints if the Python wheel becomes a supported interface.
- Record a demo video and publish a signed GitHub Release archive.
- Add a machine-readable provenance statement for frozen public validation artifacts.
