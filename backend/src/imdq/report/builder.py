"""Session -> report.

The report is assembled from a structured session object, never from a chat
transcript, so every figure in it is one that a query actually returned. The
executive summary is generated last, from that object, so it cannot cite a
number that was not computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jinja2 import Environment, select_autoescape

from imdq.analytics.dashboard import ChartSpec
from imdq.domain.imd import NORMALS_PERIOD, RAINFALL_DAY_NOTE
from imdq.nlq.service import QueryResult

_ENV = Environment(autoescape=select_autoescape(["html"]))

_TEMPLATE = _ENV.from_string(
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{ title }}</title>
<style>
 body{font-family:Arial,Helvetica,sans-serif;color:#1c1c1c;margin:32px;line-height:1.5}
 h1{font-size:20px;margin-bottom:2px} h2{font-size:16px;margin-top:28px}
 .meta{color:#555;font-size:12px;margin-bottom:24px}
 table{border-collapse:collapse;margin:10px 0;font-size:13px}
 th,td{border:1px solid #ccc;padding:4px 10px;text-align:left}
 th{background:#f0f0f0}
 .q{font-weight:bold;margin-top:18px}
 .prov{color:#666;font-size:11px;margin-top:4px}
 .note{color:#444;font-size:12px}
 .warn{color:#8a1f1f;font-size:12px}
</style></head><body>
<h1>{{ title }}</h1>
<div class="meta">Prepared {{ generated_at }} &middot; {{ organisation }}
 &middot; normals period {{ normals_period }}</div>

<h2>Executive summary</h2>
<ul>{% for line in summary %}<li>{{ line }}</li>{% endfor %}</ul>

{% if datasets %}<h2>Data sources</h2>
<table><tr><th>File</th><th>Sheet</th><th>Rows</th><th>As of</th></tr>
{% for d in datasets %}<tr><td>{{ d.filename }}</td><td>{{ d.sheet }}</td>
<td>{{ d.rows }}</td><td>{{ d.as_of }}</td></tr>{% endfor %}</table>{% endif %}

<h2>Queries and results</h2>
{% for item in queries %}
 <div class="q">{{ loop.index }}. {{ item.question }}</div>
 <div>{{ item.headline }}</div>
 {% if item.rows %}<table>
  <tr>{% for c in item.columns %}<th>{{ c }}</th>{% endfor %}</tr>
  {% for row in item.rows %}<tr>{% for c in item.columns %}<td>{{ row[c] }}</td>{% endfor %}</tr>
  {% endfor %}</table>{% endif %}
 {% if item.assumptions %}<div class="note">{{ item.assumptions|join(" ") }}</div>{% endif %}
 <div class="prov">{{ item.provenance }}</div>
{% endfor %}

{% if charts %}<h2>Figures</h2>
<ul>{% for c in charts %}<li>{{ c.title }} &mdash; {{ c.caption }}</li>{% endfor %}</ul>{% endif %}

{% if caveats %}<h2>Notes and caveats</h2>
<ul>{% for c in caveats %}<li class="warn">{{ c }}</li>{% endfor %}</ul>{% endif %}
</body></html>"""
)


@dataclass(slots=True)
class ReportSession:
    title: str = "Data analysis report"
    organisation: str = "India Meteorological Department \u00b7 SATMET Division"
    queries: list[QueryResult] = field(default_factory=list)
    charts: list[ChartSpec] = field(default_factory=list)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def add(self, result: QueryResult) -> None:
        self.queries.append(result)


def _summary_lines(session: ReportSession) -> list[str]:
    lines = [
        f"{len(session.queries)} quer{'y' if len(session.queries) == 1 else 'ies'} were run "
        f"against {len(session.datasets)} ingested table(s)."
    ]
    for result in session.queries[:6]:
        lines.append(result.answer.headline)
    if any("mm" in (q.plan.measure.unit or "") for q in session.queries if q.plan.measure):
        lines.append(f"Rainfall figures follow the {RAINFALL_DAY_NOTE.lower()} convention.")
    return lines


def render_html(session: ReportSession) -> str:
    return _TEMPLATE.render(
        title=session.title,
        organisation=session.organisation,
        generated_at=datetime.now(timezone.utc).strftime("%d %B %Y %H:%M UTC"),
        normals_period=NORMALS_PERIOD,
        summary=_summary_lines(session),
        datasets=session.datasets,
        charts=[c.to_dict() for c in session.charts],
        caveats=session.caveats,
        queries=[
            {
                "question": result.slots.question,
                "headline": result.answer.headline,
                "columns": result.answer.columns,
                "rows": result.answer.rows,
                "assumptions": result.answer.assumptions,
                "provenance": result.answer.provenance,
            }
            for result in session.queries
        ],
    )
