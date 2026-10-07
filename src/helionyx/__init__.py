"""Helionyx: open-source hybrid renewable energy sizing for AI assistants."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("helionyx")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"

DISCLAIMER = (
    "Helionyx results are pre-feasibility estimates based on simplified models, synthetic or "
    "user-supplied data, and dated tariff and cost assumptions. They are not a substitute for "
    "detailed engineering design, bankable energy yield assessment, or review and sign-off by a "
    "chartered engineer. Always verify tariffs and connection rules with the relevant utility and "
    "regulator."
)
