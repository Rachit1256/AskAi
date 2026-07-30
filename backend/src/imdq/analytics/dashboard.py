"""Automatic dashboard generation.

Emits Vega-Lite *specifications*, not images. The previous design rendered
matplotlib PNGs into a directory keyed by UUID that nothing ever cleaned up;
specs are a few hundred bytes, render natively in the browser, stay interactive,
and cost the server nothing.

Candidates are scored on how much they would actually tell a forecaster, then
de-duplicated so six near-identical bar charts cannot crowd out the one useful
scatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from imdq.storage.catalog import TableInfo
from imdq.storage.engine import SqlEngine

MAX_CHARTS = 8
MAX_CATEGORIES = 24


@dataclass(slots=True)
class ChartSpec:
    chart_id: str
    title: str
    kind: str
    score: float
    table_id: str
    spec: dict[str, Any]
    caption: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_id": self.chart_id, "title": self.title, "kind": self.kind,
            "score": round(self.score, 3), "table_id": self.table_id,
            "caption": self.caption, "warnings": self.warnings, "vega_lite": self.spec,
        }


def _vega(mark: str, values: list[dict[str, Any]], x: str, y: str, x_type: str) -> dict[str, Any]:
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": values},
        "mark": {"type": mark, "tooltip": True},
        "encoding": {
            "x": {"field": x, "type": x_type, "title": x},
            "y": {"field": y, "type": "quantitative", "title": y},
        },
        "width": "container",
        "height": 240,
    }


def _spread(values: list[float]) -> float:
    """Coefficient of variation: a flat series is not worth a chart."""
    clean = [v for v in values if isinstance(v, (int, float))]
    if len(clean) < 2:
        return 0.0
    mean = sum(clean) / len(clean)
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in clean) / len(clean)
    return (variance**0.5) / abs(mean)


def build_dashboard(
    engine: SqlEngine, tables: list[TableInfo], max_charts: int = MAX_CHARTS
) -> list[ChartSpec]:
    candidates: list[ChartSpec] = []

    for table in tables:
        measures = table.measures()
        if not measures:
            continue
        dimensions = table.dimensions() + table.time_columns()

        for measure in measures[:6]:
            slug = measure["slug"]
            row = engine.fetch_dicts(
                f'SELECT COUNT("{slug}") AS n, SUM("{slug}") AS total, '
                f'AVG("{slug}") AS mean, MIN("{slug}") AS lo, MAX("{slug}") AS hi '
                f'FROM "{table.physical_name}"'
            )
            if not row or not row[0]["n"]:
                continue
            stats = row[0]
            candidates.append(
                ChartSpec(
                    chart_id=f"kpi_{table.table_id}_{slug}",
                    title=f"{measure['name']} ({table.sheet})",
                    kind="kpi",
                    score=0.35,
                    table_id=table.table_id,
                    spec={
                        "kind": "kpi",
                        "value": stats["total"],
                        "mean": stats["mean"],
                        "min": stats["lo"],
                        "max": stats["hi"],
                        "count": stats["n"],
                        "unit": measure["unit"],
                    },
                    caption=f"{stats['n']} observations",
                )
            )

            for dimension in dimensions[:4]:
                dim_slug = dimension["slug"]
                if dim_slug == slug:
                    continue
                rows = engine.fetch_dicts(
                    f'SELECT "{dim_slug}" AS category, SUM("{slug}") AS value '
                    f'FROM "{table.physical_name}" WHERE "{slug}" IS NOT NULL '
                    f'GROUP BY "{dim_slug}" ORDER BY value DESC LIMIT {MAX_CATEGORIES}'
                )
                if len(rows) < 2:
                    continue
                spread = _spread([r["value"] for r in rows])
                is_time = dimension["role"] == "time"
                mark = "line" if is_time else "bar"
                score = 0.5 + 0.3 * min(spread, 1.0) + (0.15 if is_time else 0.0)
                if len(rows) > MAX_CATEGORIES - 1:
                    score -= 0.1
                candidates.append(
                    ChartSpec(
                        chart_id=f"{mark}_{table.table_id}_{slug}_{dim_slug}",
                        title=f"{measure['name']} by {dimension['name']}",
                        kind=mark,
                        score=score,
                        table_id=table.table_id,
                        spec=_vega(
                            mark, rows, "category", "value",
                            "temporal" if is_time
                                and dimension["sql_type"] != "VARCHAR" else "nominal",
                        ),
                        caption=f"{len(rows)} categories \u00b7 {table.filename} / {table.sheet}",
                    )
                )

    candidates.sort(key=lambda c: c.score, reverse=True)
    chosen: list[ChartSpec] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        signature = (candidate.kind, candidate.title)
        if signature in seen:
            continue
        seen.add(signature)
        chosen.append(candidate)
        if len(chosen) >= max_charts:
            break
    return chosen


@dataclass(slots=True)
class Suggestion:
    kind: str
    title: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "title": self.title, "reason": self.reason}


def suggest_visualisations(tables: list[TableInfo]) -> list[Suggestion]:
    """Chart shapes the data supports but the default dashboard did not spend a
    slot on. Each carries the reason it fits, so the reader can judge it."""
    out: list[Suggestion] = []
    for table in tables:
        measures = table.measures()
        times = table.time_columns()
        dimensions = table.dimensions()

        if len(measures) >= 2:
            out.append(
                Suggestion(
                    "scatter",
                    f"{measures[0]['name']} against {measures[1]['name']}",
                    "Two measures on the same rows can be correlated directly.",
                )
            )
        if times and measures:
            out.append(
                Suggestion(
                    "line",
                    f"{measures[0]['name']} over {times[0]['name']}",
                    "A time column is present, so a trend is well defined.",
                )
            )
        for dimension in dimensions[:2]:
            sample = dimension.get("distinct_sample") or []
            if 2 <= len(sample) <= 8 and measures:
                out.append(
                    Suggestion(
                        "pie",
                        f"Share of {measures[0]['name']} by {dimension['name']}",
                        f"{dimension['name']} has few enough values to read as a share.",
                    )
                )
        if any(m["unit"] == "mm" for m in measures):
            out.append(
                Suggestion(
                    "bar",
                    "Departure from the 1991-2020 normal",
                    "Rainfall is present; a departure chart is the standard IMD view.",
                )
            )
    seen: set[tuple[str, str]] = set()
    unique: list[Suggestion] = []
    for item in out:
        if (item.kind, item.title) in seen:
            continue
        seen.add((item.kind, item.title))
        unique.append(item)
    return unique[:8]
