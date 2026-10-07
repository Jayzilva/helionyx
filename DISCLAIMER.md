# Disclaimer

Please read this before using Helionyx or any result it produces.

## 1. Pre-feasibility estimates only

Helionyx produces **pre-feasibility estimates** from simplified models, synthetic or
user-supplied data, and dated tariff and cost assumptions. Its results are **not**:

- detailed engineering design, a bankable energy yield assessment or an investment
  recommendation;
- a grid-connection, protection, safety or code-compliance study;
- a substitute for review and sign-off by a qualified, chartered engineer.

Do not use Helionyx results as the sole basis for financial, purchasing, engineering,
safety or contractual decisions.

## 2. Placeholder data

The bundled Sri Lankan data pack (`lk`) contains **unverified placeholder values** for tariff
rates, time-of-use windows, export-scheme rules, component costs, fuel prices, discount rates
and emission factors. They exist so the software can be built and tested. `validate_scenario`
raises warning HNX-W001 for every unverified tariff. Always confirm tariffs and connection
rules with the relevant utility and regulator, and costs with suppliers.

## 3. No warranty and no liability

Helionyx is provided **"AS IS", without warranties or conditions of any kind**, express or
implied, including fitness for a particular purpose, accuracy and non-infringement. To the
extent permitted by law, the authors and contributors are **not liable** for any damages or
losses arising from its use, including financial loss, equipment damage, or decisions made
on its results. See sections 7 and 8 of the [Apache License 2.0](LICENSE).

## 4. AI assistants

Helionyx is designed to be used through AI assistants. The server computes every number
deterministically, but an assistant can still misquote, omit or misinterpret results.
Check figures against the tool outputs and run IDs. Parts of this software and its
documentation were written with AI assistance and reviewed by the maintainer.

## 5. External services and your data

Helionyx runs locally. Some features send data to third parties under their own terms:

| Feature | Service | Data sent |
|---|---|---|
| `fetch_resource` | NASA POWER, PVGIS (EU JRC) | Site latitude and longitude |
| `run_optimization` with `solver: "reopt"` | NREL REopt API | Hourly load, PV production, tariff-derived prices, costs, your API key |

Use `HNX_OFFLINE=1` to prevent all network calls. You are responsible for having the right
to process any measured load or site data you import, and for complying with the terms of
the services you call.

## 6. Security

The Streamable HTTP mode's bearer-key protection (`HNX_API_KEY`) is development-grade. Do not
expose Helionyx to the internet. See [SECURITY.md](SECURITY.md).

## 7. No affiliation and trademarks

Helionyx is an independent open-source project. It is **not affiliated with, endorsed by or
sponsored by** UL Solutions or HOMER Energy (HOMER Pro), the U.S. National Renewable Energy
Laboratory (REopt), NASA (POWER), the European Commission Joint Research Centre (PVGIS), the
Ceylon Electricity Board (CEB), Lanka Electricity Company (LECO), the Public Utilities
Commission of Sri Lanka, Anthropic, or the authors of MicroGridsPy or SAMA. All product names
and trademarks belong to their owners and are used only to describe compatibility or
comparison. "HOMER-style" describes a general method, not a reproduction of HOMER Pro.

This is a personal project. It does not represent the views of, and is not a product of,
any employer of its authors.

## 8. Third-party licences

The core is licensed under Apache-2.0. The optional adapter packages `helionyx-microgridspy`
(EUPL-1.2) and `helionyx-sama` (AGPL-3.0) are separate works with their own licences; the
core never imports them. Data-pack records carry their own source and licence notes.
