"""Deterministic graders. Gold is never produced by an LLM."""

from __future__ import annotations

import math
import re
from typing import Any

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_ISO_DATE = re.compile(r"\b(?:19|20)\d{2}[-/\u2013\u2014.]\d{1,2}[-/\u2013\u2014.]\d{1,2}\b")
_YEAR_QUARTER = re.compile(r"\b(?:19|20)\d{2}\s*Q[1-4]\b", re.IGNORECASE)
_QUARTER_YEAR = re.compile(r"\bQ[1-4]\s*(?:19|20)\d{2}\b", re.IGNORECASE)
_BARE_QUARTER = re.compile(r"\bQ[1-4]\b", re.IGNORECASE)


def _strip_temporal(text: str) -> str:
    """Drop dates and year-quarter mentions so they are not scored as the answer."""
    cleaned = (text or "").replace(",", "")
    cleaned = _ISO_DATE.sub(" ", cleaned)
    cleaned = _YEAR_QUARTER.sub(" ", cleaned)
    cleaned = _QUARTER_YEAR.sub(" ", cleaned)
    cleaned = _BARE_QUARTER.sub(" ", cleaned)
    return cleaned


def _floats(text: str) -> list[float]:
    return [float(m) for m in _NUM.findall(_strip_temporal(text))]


def _query_noise(query: str | None) -> set[float]:
    if not query:
        return set()
    # Raw query digits (years, months, Q2) plus anything left after temporal strip.
    raw = (query or "").replace(",", "")
    return {float(m) for m in _NUM.findall(raw)} | set(_floats(query))


def extract_number(text: str, query: str | None = None) -> float | None:
    matches = _floats(text)
    if not matches:
        return None
    noise = _query_noise(query)
    if noise:
        filtered = [m for m in matches if m not in noise]
        if filtered:
            matches = filtered
    return matches[0]


def extract_int(text: str, query: str | None = None) -> int | None:
    n = extract_number(text, query=query)
    return None if n is None else int(round(n))


def looks_like_abstain(text: str) -> bool:
    t = text.lower()
    return any(
        s in t
        for s in (
            "not found",
            "cannot find",
            "can't find",
            "insufficient",
            "do not have",
            "don't have",
            "no such employee",
            "no such record",
            "no such person",
            "unknown",
            "abstain",
            "cannot answer",
            "can't answer",
            "not in the",
            "no employee",
            "no record exists",
            "non-existent",
            "does not exist",
            "did not exist",
            "do not exist",
            "no board meeting",
            "not scheduled",
            "cannot retrieve a salary",
            "cannot retrieve their salary",
            "had no revenue",
            "no revenue",
            "no data available",
            "no data found",
            "no attendees",
            "no records found",
            "no events found",
            "no matching",
        )
    )


def grade(task: dict[str, Any], answer: str) -> dict[str, Any]:
    kind = task.get("grader_type") or "exact_int"
    gold = task.get("gold")
    query = task.get("query")
    if kind == "abstain":
        halluc = extract_number(answer, query=query) is not None and not looks_like_abstain(answer)
        ok = looks_like_abstain(answer) and not halluc
        return {"ok": ok, "grader": kind, "gold": "ABSTAIN", "predicted": answer[:200], "hallucinated_number": halluc}
    if kind == "exact_int":
        pred = extract_int(answer, query=query)
        ok = pred is not None and int(gold) == pred
        return {"ok": ok, "grader": kind, "gold": int(gold), "predicted": pred}
    if kind == "numeric_rel":
        pred = extract_number(answer, query=query)
        if pred is None or gold in (None, ""):
            return {"ok": False, "grader": kind, "gold": gold, "predicted": pred}
        gold_f = float(gold)
        if gold_f == 0:
            ok = abs(pred) < 1e-6
        else:
            ok = abs(pred - gold_f) / abs(gold_f) <= 0.005
        return {"ok": ok, "grader": kind, "gold": gold_f, "predicted": pred}
    if kind == "set_eq":
        gold_set = set(str(x) for x in (gold or []))
        found = set(re.findall(r"[A-Za-z0-9_\-]+", answer))
        ok = gold_set <= found if gold_set else False
        return {"ok": ok, "grader": kind, "gold": sorted(gold_set), "predicted": sorted(found)}
    if kind == "exact_float":
        pred = extract_number(answer, query=query)
        ok = pred is not None and gold is not None and math.isclose(float(pred), float(gold), rel_tol=0, abs_tol=0.05)
        return {"ok": ok, "grader": kind, "gold": gold, "predicted": pred}
    return {"ok": False, "grader": kind, "error": f"unknown grader {kind}"}
