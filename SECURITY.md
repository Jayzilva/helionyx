# Security policy

## Supported versions

Security fixes go into the latest release on PyPI and the `main` branch.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub:
**Security → Report a vulnerability** on
[github.com/Jayzilva/helionyx](https://github.com/Jayzilva/helionyx/security/advisories/new).

Include the version, steps to reproduce and the impact you expect. You should get a first
reply within 7 days. Once a fix is released, the advisory is published with credit unless
you ask otherwise.

## Scope notes

- Helionyx is designed to run **locally** (stdio). Streamable HTTP mode with `HNX_API_KEY` is
  development-grade protection only: bind it to localhost or a trusted network. OAuth 2.1 with
  Microsoft Entra ID is planned for hosted mode (v1.0).
- File paths are restricted to the workspace (`HNX_WORKSPACE`); report any way around that.
- API keys (`HNX_REOPT_API_KEY`, `HNX_API_KEY`) are read from the environment and never
  written to results or logs; report any leak.
