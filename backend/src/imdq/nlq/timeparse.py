"""Time expressions, including IMD's four official seasons.

Returns a declarative :class:`TimeSpec` rather than SQL, so the same parse can
drive a filter, a comparison or a trend without re-parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from imdq.domain.imd import MONTH_ABBR, MONTH_NAMES, SEASONS

_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_YEAR_RANGE = re.compile(
    r"\b(1[89]\d{2}|20\d{2})\s*(?:-|to|until|through|and)\s*(1[89]\d{2}|20\d{2})\b"
)
_LAST_N = re.compile(r"\blast\s+(\d{1,3})\s+(year|month|day)s?\b")

#: (alias, season) sorted longest-first so specific phrases win over generic ones.
_SEASON_ALIASES = sorted(
    (
        (alias, season)
        for season in SEASONS
        for alias in (season.code.lower(), season.label.lower(), *season.aliases)
    ),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


@dataclass(slots=True)
class TimeSpec:
    months: tuple[int, ...] = ()
    years: tuple[int, ...] = ()
    year_from: int | None = None
    year_to: int | None = None
    season_code: str | None = None
    label: str = "all available periods"
    relative_days: int | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.months or self.years or self.year_from or self.season_code or self.relative_days
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "months": list(self.months),
            "years": list(self.years),
            "year_from": self.year_from,
            "year_to": self.year_to,
            "season": self.season_code,
            "label": self.label,
        }


def parse_time(text: str, today: date | None = None) -> tuple[TimeSpec, str]:
    """Extract a time expression, returning it and the text with it removed.

    Removing the matched span matters: leaving "july" in the residue makes the
    lexicon match a column called July and the question resolves twice.
    """
    today = today or date.today()
    lowered = f" {text.lower()} "
    spec = TimeSpec()
    consumed: list[tuple[int, int]] = []

    # Longest alias first: "post monsoon" must not be swallowed by "monsoon".
    for alias, season in _SEASON_ALIASES:
        index = lowered.find(f" {alias} ")
        if index >= 0:
            spec.season_code = season.code
            spec.months = season.months
            spec.label = f"{season.label} season ({season.code})"
            consumed.append((index, index + len(alias) + 2))
            break

    if not spec.months:
        found_months = [
            (lowered.find(f" {name} "), MONTH_ABBR[name])
            for name in MONTH_ABBR
            if f" {name} " in lowered
        ]
        if found_months:
            spec.months = tuple(sorted({m for _, m in found_months}))
            names = [MONTH_NAMES[m - 1] for m in spec.months]
            spec.label = " and ".join(names)
            consumed += [(i, i + 10) for i, _ in found_months if i >= 0]

    if match := _YEAR_RANGE.search(lowered):
        spec.year_from, spec.year_to = int(match.group(1)), int(match.group(2))
        spec.label = f"{spec.label}, {spec.year_from}-{spec.year_to}".lstrip(", ")
        consumed.append(match.span())
    elif years := _YEAR.findall(lowered):
        spec.years = tuple(sorted({int(y) for y in years}))
        joined = ", ".join(str(y) for y in spec.years)
        spec.label = f"{spec.label}, {joined}" if spec.months or spec.season_code else joined
        for match in _YEAR.finditer(lowered):
            consumed.append(match.span())

    if match := _LAST_N.search(lowered):
        count, unit = int(match.group(1)), match.group(2)
        if unit == "year":
            spec.year_from, spec.year_to = today.year - count + 1, today.year
            spec.label = f"last {count} years"
        else:
            spec.relative_days = count * (30 if unit == "month" else 1)
            spec.label = f"last {count} {unit}s"
        consumed.append(match.span())

    residue = lowered
    for start, end in sorted(consumed, reverse=True):
        residue = residue[:start] + " " + residue[end:]
    return spec, " ".join(residue.split())
