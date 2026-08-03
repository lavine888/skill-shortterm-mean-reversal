# Roadmap

## Completed in 0.5.0: Independent Factor Evidence

- Materialized a frozen Parquet table for every decision-date cross-section.
- Included symbol, trailing return, reversal score, group, entry/exit price and forward return.
- Bound the evidence table to JSON with row counts, schema version and SHA-256.
- Extended the validator to reconstruct deciles, Rank IC and coverage from that table.

The remaining work is to freeze and publish a provider-permitted multi-year evidence artifact, not to expand the contract further.

## P1: Data and Execution Evidence

- Freeze an official SH/SZ market calendar instead of relying on panel-date union.
- Add historical point-in-time suspension, ST and limit-up/limit-down states.
- Validate the historical universe against an independent exchange source.
- Add delisting announcement/last-trading-day evidence visible before execution.
- Model borrow availability, fees and recalls for the short leg.

## P1: Research Validation

- Run a multi-year full-market sample with rolling and chronological out-of-sample splits.
- Report cost, holding-period, decile-width and market-regime sensitivity.
- Compare long-short research returns with executable long-only and index-hedged variants.

## P2: Distribution

- Add installed console entrypoints if the Python wheel becomes a supported interface.
- Record a demo video and publish a signed GitHub Release archive.
- Add a machine-readable provenance statement for frozen public validation artifacts.
