# Contributing to Helionyx

Thank you for helping. Helionyx is small on purpose: small modules, tests for every
equation and data with a source and a date.

## Development setup

```bash
uv venv
uv pip install -e ".[dev]"
```

Python 3.11–3.13 on Windows, macOS or Linux.

## Checks

Run these before opening a pull request:

```bash
ruff check src tests
mypy src/helionyx/core
HNX_OFFLINE=1 pytest
helionyx pack validate src/helionyx/packs/lk
```

- `core/` is type-checked with mypy in strict mode.
- Tests run offline using the bundled NASA POWER samples. Mark tests that need the network
  with `@pytest.mark.network`.
- Engine and billing changes need unit tests against hand calculations; dispatch changes
  must keep the property tests (energy balance ≤ 0.001 kWh, SOC bounds) green.
- If a change alters results intentionally, regenerate the regression goldens and explain
  why in the pull request.

## Code conventions

- Keep the layers: `api` → `services` → `core` / `infra`. `core` must not import from `services` or `api`.
- No country-specific literals (utility names, tariff codes, costs) in engine code; they
  belong in data packs.
- Every numeric output field carries its unit in its name or a `units` map.
- Errors use `HelionyxError` with a code from `helionyx.errors.ErrorCode` and an actionable hint.
- The server never calls an LLM.

## Data-pack contributions

See [docs/data-pack-guide.md](docs/data-pack-guide.md). Every record must cite its source
document, URL and retrieval date. Mark a record `status: verified` only after a second
person has checked it against the source. Data packs are licensed CC BY 4.0.

## Licensing

- Core code is Apache-2.0. By contributing you agree your contribution is licensed under it.
- **No GPL or AGPL dependencies in the core package.** CI fails if one appears. Copyleft
  solvers (for example SAMA) must live in separate, optional packages run as subprocesses.

## Commits and pull requests

Use short, imperative commit messages (`feat: add LECO tariff revision`). Describe what
changed, why, and how you tested it.
