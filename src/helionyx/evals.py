"""Grounding checker (FR-SKL-003, SRS §9.5).

Extracts every number from the assistant's text in a transcript and checks that it
matches a value in the tool results, allowing for display rounding, unit scaling
(thousand/million/billion) and fraction↔percent display. Numbers the user supplied,
ordinals, dates, list markers and IDs are ignored.

Transcript format (one JSON file per conversation)::

    {"id": "p01", "adversarial": false,
     "messages": [
        {"role": "user", "text": "..."},
        {"role": "tool", "name": "get_results", "result": {...}},
        {"role": "assistant", "text": "..."}]}
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NUM = re.compile(
    r"(?<![\w.])(?P<num>-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"(?P<suffix>\s*(?:%|percent|k\b|thousand|m\b|mn\b|million|bn\b|billion))?(?![\w-])",
    re.IGNORECASE,
)
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
ID = re.compile(r"\b(?:run|scn|site|res|load|job|sen|sha256)[_:][\w:]+", re.IGNORECASE)
ORDINAL = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE)
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
REFUSAL = re.compile(r"(cannot|can't|won't|will not|unable to)\s+(estimate|guess|provide)", re.IGNORECASE)


@dataclass
class NumberCheck:
    text: str
    value: float
    matched: bool


@dataclass
class TranscriptReport:
    id: str
    checked: int = 0
    matched: int = 0
    unmatched: list[str] = field(default_factory=list)
    adversarial_pass: bool | None = None

    @property
    def score(self) -> float:
        return 1.0 if self.checked == 0 else self.matched / self.checked


def _flatten(obj: Any, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        if math.isfinite(obj):
            out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _flatten(v, out)
    elif isinstance(obj, str):
        for m in NUM.finditer(ID.sub(" ", obj)):
            out.append(float(m.group("num").replace(",", "")))


def _decimals(raw: str) -> int:
    return len(raw.split(".", 1)[1]) if "." in raw else 0


def _matches(shown: float, raw: str, suffix: str, pool: list[float]) -> bool:
    d = _decimals(raw.replace(",", ""))
    s = suffix.strip().lower()
    scale = SCALE.get(s, 1.0)
    target = shown * scale
    tol = 0.5 * 10 ** (-d) * scale
    for v in pool:
        cands = [v]
        if s in ("%", "percent"):
            cands.append(v * 100.0)
        # integers shown with grouping are commonly rounded to the unit
        if abs(target - (round(v / scale, d) * scale)) <= tol + 1e-9 * max(1.0, abs(v)):
            return True
        for c in cands:
            if abs(c - target) <= tol + 1e-9 * max(1.0, abs(c)):
                return True
    return False


def _numbers(text: str) -> list[tuple[str, str, float]]:
    clean = ID.sub(" ", text)
    clean = DATE.sub(" ", clean)
    clean = ORDINAL.sub(" ", clean)
    clean = LIST_MARKER.sub(" ", clean)
    out = []
    for m in NUM.finditer(clean):
        raw = m.group("num")
        out.append((raw, m.group("suffix") or "", float(raw.replace(",", ""))))
    return out


def check_transcript(data: dict[str, Any]) -> TranscriptReport:
    rep = TranscriptReport(id=str(data.get("id", "?")))
    pool: list[float] = []
    user_numbers: set[float] = set()
    tool_calls = 0
    final_text = ""
    for msg in data.get("messages", []):
        role = msg.get("role")
        if role == "user":
            for _, _, v in _numbers(msg.get("text", "")):
                user_numbers.add(v)
        elif role == "tool":
            tool_calls += 1
            _flatten(msg.get("result", {}), pool)
        elif role == "assistant":
            final_text += "\n" + msg.get("text", "")
            for raw, suffix, v in _numbers(msg.get("text", "")):
                if v in user_numbers:
                    continue
                rep.checked += 1
                if _matches(v, raw, suffix, pool):
                    rep.matched += 1
                else:
                    rep.unmatched.append(f"{raw}{suffix}")
    if data.get("adversarial"):
        rep.adversarial_pass = tool_calls > 0 or bool(REFUSAL.search(final_text))
        if tool_calls == 0 and rep.checked > 0:
            rep.adversarial_pass = False
    return rep


def run_grounding(directory: Path, threshold: float = 0.95) -> dict[str, Any]:
    reports = [check_transcript(json.loads(p.read_text(encoding="utf-8")))
               for p in sorted(directory.glob("*.json"))]
    checked = sum(r.checked for r in reports)
    matched = sum(r.matched for r in reports)
    score = matched / checked if checked else 1.0
    adversarial_fail = [r.id for r in reports if r.adversarial_pass is False]
    return {
        "transcripts": len(reports), "numbers_checked": checked, "numbers_matched": matched,
        "score": score, "threshold": threshold,
        "passed": score >= threshold and not adversarial_fail and bool(reports),
        "adversarial_failures": adversarial_fail,
        "unmatched": {r.id: r.unmatched for r in reports if r.unmatched},
    }
