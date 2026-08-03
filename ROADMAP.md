# Roadmap

## Completed in 0.7.0: Deferred Exit and Daily NAV

- Carry positions that cannot exit at limit-down, limit-up or suspension instead of assuming a fill.
- Mark blocked positions daily and defer liquidation until the first directionally executable session.
- Prevent new target weights from spending capital still locked in carried positions.
- Replace sequential five-day period compounding with daily NAV and explicit order/position ledgers.
- Preserve every blocked order, retry date, fill assumption and cost in auditable evidence.

The ledger now carries and retries ordinary blocked exits. It still fails closed when an open security disappears after delisting without a verifiable settlement value.

## P0: Delisting Settlement Evidence

- Source exchange-backed delisting settlement or transfer-board values.
- Distinguish cash cancellation, transfer to another venue and unresolved beneficial ownership.
- Replay settlement cash flows without retroactively changing prior NAV marks.
- Keep `error` as the default when no independently verifiable settlement value exists.

## Completed in 0.5.0: Independent Factor Evidence

- Materialized a frozen Parquet table for every decision-date cross-section.
- Included symbol, trailing return, reversal score, group, entry/exit price and forward return.
- Bound the evidence table to JSON with row counts, schema version and SHA-256.
- Extended the validator to reconstruct deciles, Rank IC and coverage from that table.

The remaining work is to freeze and publish a provider-permitted multi-year evidence artifact, not to expand the contract further.

## P1: Data and Execution Evidence

- Validate the historical universe against an independent exchange source.
- Add delisting announcement/last-trading-day evidence visible before execution.
- Model borrow availability, fees and recalls for the short leg.

## Completed in 0.6.0: Calendar and Daily Trading States

- Integrated and cached the official PandaData SH trading calendar.
- Mapped daily `trade_status`, historical ST names and adjusted limit prices.
- Added side-aware entry and exit constraints for limit-up and limit-down sessions.
- Bound trading states, limit prices and block reasons into JSON and full factor evidence.

## P1: Research Validation

- Run a multi-year full-market sample with rolling and chronological out-of-sample splits.
- Report cost, holding-period, decile-width and market-regime sensitivity.
- Compare long-short research returns with executable long-only and index-hedged variants.

## P2: Distribution

- Add installed console entrypoints if the Python wheel becomes a supported interface.
- Record a demo video and publish a signed GitHub Release archive.
- Add a machine-readable provenance statement for frozen public validation artifacts.
