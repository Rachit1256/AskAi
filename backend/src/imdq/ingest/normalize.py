"""Value coercion, column typing, derived-column detection and unpivoting.

Operates on plain row tuples rather than DataFrames so the pipeline can stream a
sheet of any size. Meteorological conventions (trace rainfall, missing
sentinels, derived seasonal columns) are enforced here rather than left to
pandas' default coercion, which would turn a trace day into a null and a
sentinel into a real observation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Iterable, NamedTuple, Sequence

from imdq.domain.imd import (
    CANONICAL_UNITS,
    SEASON_BY_CODE,
    DERIVED_COLUMN_TOKENS,
    MISSING_SENTINELS,
    MONTH_ABBR,
    MONTH_NAMES,
    TRACE_MARKERS,
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_UNIT_IN_NAME = re.compile(r"[\(\[]\s*([a-zA-Z°%/]+)\s*[\)\]]")
_NUMBER_CLEAN = re.compile(r"[,\s\u20b9$\u20ac\u00a3\u00a5]")

DERIVED_TOLERANCE = 0.02  # relative agreement required to confirm a total column


class ColumnRole(StrEnum):
    IDENTIFIER = "identifier"
    DIMENSION = "dimension"
    MEASURE = "measure"
    TIME = "time"


class ParsedValue(NamedTuple):
    value: float | str | date | None
    is_missing: bool
    is_trace: bool


@dataclass(slots=True)
class ColumnSpec:
    source_name: str
    slug: str
    role: ColumnRole
    unit: str | None = None
    period_label: str | None = None
    sql_type: str = "VARCHAR"
    is_derived: bool = False
    derived_of: list[str] = field(default_factory=list)
    distinct_sample: list[str] = field(default_factory=list)
    null_fraction: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.source_name,
            "slug": self.slug,
            "role": str(self.role),
            "unit": self.unit,
            "period_label": self.period_label,
            "sql_type": self.sql_type,
            "is_derived": self.is_derived,
            "derived_of": list(self.derived_of),
            "null_fraction": round(self.null_fraction, 4),
        }


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    slug = _NON_ALNUM.sub("_", ascii_name.strip().lower()).strip("_")
    return slug or "col"


def parse_number(raw: Any) -> ParsedValue:
    """Coerce one cell, preserving the distinction between trace and missing."""
    if raw is None:
        return ParsedValue(None, True, False)
    if isinstance(raw, bool):
        return ParsedValue(float(raw), False, False)
    if isinstance(raw, (int, float)):
        number = float(raw)
        if number in MISSING_SENTINELS:
            return ParsedValue(None, True, False)
        return ParsedValue(number, False, False)

    text = str(raw).strip()
    if not text:
        return ParsedValue(None, True, False)
    if text.lower() in TRACE_MARKERS:
        # A real observation of less than 0.1 mm, not an absent one.
        return ParsedValue(0.0, False, True)

    negative = text.startswith("(") and text.endswith(")")
    cleaned = _NUMBER_CLEAN.sub("", text.strip("()"))
    percent = cleaned.endswith("%")
    cleaned = cleaned.rstrip("%")
    try:
        number = float(cleaned)
    except ValueError:
        return ParsedValue(text, False, False)
    if negative:
        number = -number
    if percent:
        number /= 100.0
    if number in MISSING_SENTINELS:
        return ParsedValue(None, True, False)
    return ParsedValue(number, False, False)


def infer_unit(column_name: str) -> str | None:
    match = _UNIT_IN_NAME.search(column_name)
    if match:
        token = match.group(1).lower()
        return {"c": "degC", "°c": "degC", "t": "tonnes", "mm": "mm", "%": "percent"}.get(
            token, token
        )
    lowered = column_name.lower()
    for concept, unit in CANONICAL_UNITS.items():
        if concept.replace("_", " ") in lowered:
            return unit
    return None


#: Column names that identify the *row's* time key rather than a measurement.
TIME_KEY_NAMES = frozenset(
    {"year", "date", "month", "day", "time", "period", "datetime", "timestamp",
     "as_of", "as_of_date", "obs_date", "observation_date", "yr"}
)


def is_period_label(label: str) -> bool:
    """True when a *column header* is itself a period value (Jan, 1901, JJAS).

    Such a column holds measurements whose time coordinate lives in the header,
    so it is a measure that must be unpivoted -- not a time key.
    """
    token = str(label).strip().lower()
    return token in MONTH_ABBR or bool(re.fullmatch(r"(19|20)\d{2}", token))


def classify_columns(header: list[str], sample_rows: list[tuple[Any, ...]]) -> list[ColumnSpec]:
    """Assign a role, unit and null fraction to every column from a sample."""
    specs: list[ColumnSpec] = []
    n = len(sample_rows) or 1

    for index, name in enumerate(header):
        column = [row[index] if index < len(row) else None for row in sample_rows]
        parsed = [parse_number(value) for value in column]
        missing = sum(1 for p in parsed if p.is_missing)
        numeric = sum(1 for p in parsed if isinstance(p.value, float))
        present = max(1, len(parsed) - missing)
        distinct = {str(p.value) for p in parsed if p.value is not None}

        slug = slugify(name)
        period_label = is_period_label(name)

        if not period_label and (
            slug in TIME_KEY_NAMES
            or any(isinstance(value, (datetime, date)) for value in column)
        ):
            role = ColumnRole.TIME
        elif period_label or numeric / present >= 0.8:
            role = (
                ColumnRole.IDENTIFIER
                if len(distinct) == present and present > 2 and "id" in name.lower()
                else ColumnRole.MEASURE
            )
        else:
            role = ColumnRole.DIMENSION

        year_like = role is ColumnRole.TIME and bool(parsed) and all(
            isinstance(p.value, float) and p.value.is_integer() and 1800 <= p.value <= 2200
            for p in parsed
            if p.value is not None
        )
        sql_type = (
            "DOUBLE" if role is ColumnRole.MEASURE
            else "BIGINT" if year_like
            else "VARCHAR"
        )

        specs.append(
            ColumnSpec(
                source_name=str(name),
                slug=slugify(name),
                role=role,
                unit=infer_unit(str(name)) if role is ColumnRole.MEASURE else None,
                period_label=str(name).strip() if period_label else None,
                sql_type=sql_type,
                distinct_sample=sorted(distinct)[:20],
                null_fraction=missing / n,
            )
        )

    _resolve_slug_collisions(specs)
    return specs


def _resolve_slug_collisions(specs: list[ColumnSpec]) -> None:
    seen: dict[str, int] = {}
    for spec in specs:
        if spec.slug in seen:
            seen[spec.slug] += 1
            spec.slug = f"{spec.slug}_{seen[spec.slug]}"
        else:
            seen[spec.slug] = 1


def detect_derived_columns(
    specs: list[ColumnSpec], rows: Sequence[tuple[Any, ...]], max_check_rows: int = 200
) -> None:
    """Flag columns that are aggregates of their siblings.

    Rainfall series carry Annual and seasonal columns beside the monthly ones.
    Melting them together roughly doubles every total, so they are flagged here
    and excluded from the long form -- and kept as a checksum instead.

    Name matching alone is not enough (a column called "Total Stations" is a
    real measure), so a name hit is confirmed numerically against the sum of the
    candidate sibling columns.
    """
    measures = [i for i, s in enumerate(specs) if s.role is ColumnRole.MEASURE]
    if len(measures) < 3:
        return

    named = [
        i
        for i in measures
        if any(token in specs[i].slug for token in DERIVED_COLUMN_TOKENS)
    ]
    if not named:
        return

    sample = [row for _, row in zip(range(max_check_rows), rows)]
    if not sample:
        return

    base = [i for i in measures if i not in named]

    for candidate in named:
        parts_index = _sibling_columns_for(specs, candidate, base)
        if not parts_index:
            continue
        agreements = comparisons = 0
        for row in sample:
            stated = parse_number(row[candidate] if candidate < len(row) else None).value
            if not isinstance(stated, float) or stated == 0:
                continue
            parts = [parse_number(row[i] if i < len(row) else None).value for i in parts_index]
            total = sum(p for p in parts if isinstance(p, float))
            comparisons += 1
            if abs(total - stated) <= abs(stated) * DERIVED_TOLERANCE:
                agreements += 1
        if comparisons >= 3 and agreements / comparisons >= 0.8:
            specs[candidate].is_derived = True
            specs[candidate].derived_of = [specs[i].slug for i in parts_index]


def _sibling_columns_for(
    specs: list[ColumnSpec], candidate: int, base: list[int]
) -> list[int]:
    """Which columns a suspected aggregate should be compared against.

    A seasonal code (JJAS) sums only its own months, so testing it against all
    twelve would reject it and the column would be melted in as if it were an
    independent observation.
    """
    season = SEASON_BY_CODE.get(specs[candidate].slug.upper())
    if season is None:
        return base
    wanted = {MONTH_NAMES[m - 1][:3].lower() for m in season.months}
    return [i for i in base if specs[i].slug[:3].lower() in wanted]


def unpivot(
    header: list[str], rows: Iterable[tuple[Any, ...]], id_columns: int = 1
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Melt a crosstab whose column headers are values, not field names.

    Left wide, every daily file adds columns instead of rows and no schema ever
    stabilises.
    """
    value_columns = list(range(id_columns, len(header)))
    out_header = [*header[:id_columns], "period", "value"]
    out_rows: list[tuple[Any, ...]] = []
    for row in rows:
        keys = tuple(row[:id_columns])
        for index in value_columns:
            if index < len(row):
                out_rows.append((*keys, header[index], row[index]))
    return out_header, out_rows
