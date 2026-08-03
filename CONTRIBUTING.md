# Contributing

## Development

Use Python 3.11 or newer and keep changes scoped to the Q58 research contract.

```powershell
pip install -r requirements-dev.txt
pytest -q
python scripts/validate_metadata.py
```

Any change to signal timing, execution timing, holding periods, costs, missing-price handling or delisting treatment must include a focused regression test and a methodology update.

## Data Safety

Never commit PandaData credentials, raw provider responses, full-market generated output, local caches or authentication artifacts. Release packages are built from the allowlist in `scripts/package_release.ps1`.

## Research Claims

Keep software validation separate from investment evidence. Synthetic demo output proves only that the pipeline runs. Historical results must disclose the universe, dates, costs, coverage, execution assumptions and unresolved data capabilities.
