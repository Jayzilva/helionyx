"""Common solver-adapter interface (FR-ADP-001)."""

from __future__ import annotations

from typing import Any, Protocol


class SolverAdapter(Protocol):
    name: str
    version: str

    def prepare(self, scenario_doc: dict[str, Any]) -> dict[str, Any]:
        """Map a Helionyx scenario to the solver's native input."""

    def run(self, prepared: dict[str, Any], progress: Any) -> dict[str, Any]:
        """Execute the solver and return its raw output."""

    def normalise(self, raw: dict[str, Any], scenario_doc: dict[str, Any]) -> dict[str, Any]:
        """Convert raw output to a Helionyx candidate record (sizes, metrics, solver name and version)."""
