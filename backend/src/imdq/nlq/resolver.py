"""Slot filling: turn a question into typed references to real columns and values.

Deterministic and inspectable. Where two referents are equally plausible the
resolver raises rather than guessing -- a silently chosen station is a wrong
answer that looks right, which is the worst failure mode for a departmental tool.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from imdq.errors import AmbiguousQuery
from imdq.nlq.lexicon import LexHit, Lexicon, tokenise
from imdq.nlq.timeparse import TimeSpec, parse_time

AGGREGATIONS: dict[str, tuple[str, ...]] = {
    "sum": ("total", "sum", "combined", "aggregate", "cumulative", "overall"),
    "avg": ("average", "avg", "mean", "typical", "normal"),
    "max": ("max", "maximum", "highest", "peak", "largest", "wettest", "hottest"),
    "min": ("min", "minimum", "lowest", "smallest", "driest", "coolest"),
    "count": ("count", "how many", "number of", "frequency"),
}
DESCENDING = ("top", "highest", "largest", "most", "wettest", "hottest", "maximum")
ASCENDING = ("bottom", "lowest", "least", "driest", "smallest", "minimum", "coolest")

_TOP_N = re.compile(r"\b(?:top|bottom|first|highest|lowest)\s+(\d{1,4})\b")
_BY = re.compile(r"\bby\s+([a-z0-9_ ]{2,40})")
_COMPARATOR = re.compile(
    r"\b(above|over|more than|greater than|below|under|less than|at least|at most)\s+([\d.]+)"
)
_COMPARATOR_OPS = {
    "above": ">",
    "over": ">",
    "more than": ">",
    "greater than": ">",
    "below": "<",
    "under": "<",
    "less than": "<",
    "at least": ">=",
    "at most": "<=",
}
AMBIGUITY_MARGIN = 0.12

#: Words that carry no referent, excluded when judging whether a match was exact.
_STOPWORDS = frozenset(
    {
        "what",
        "is",
        "the",
        "a",
        "an",
        "of",
        "for",
        "in",
        "at",
        "on",
        "was",
        "were",
        "show",
        "me",
        "give",
        "tell",
        "how",
        "much",
        "many",
        "and",
        "to",
        "by",
        "top",
        "bottom",
        "first",
        "last",
        "please",
        "there",
        "value",
        "values",
        *(word for words in AGGREGATIONS.values() for word in words),
    }
)


@dataclass(slots=True)
class ColumnRef:
    table_id: str
    physical_name: str
    slug: str
    role: str
    display: str
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.physical_name,
            "column": self.slug,
            "display": self.display,
            "unit": self.unit,
        }


@dataclass(slots=True)
class Filter:
    column: ColumnRef
    op: str
    value: Any

    def describe(self) -> str:
        if self.op == "=":
            return f"{self.value} ({self.column.display})"
        words = {">": "above", "<": "below", ">=": "at least", "<=": "at most"}
        return f"{self.column.display} {words.get(self.op, self.op)} {self.value}"


@dataclass(slots=True)
class Candidate:
    label: str
    table_id: str
    detail: str


@dataclass(slots=True)
class Slots:
    question: str
    residue: str
    time: TimeSpec
    aggregation: str | None = None
    measure: ColumnRef | None = None
    group_by: list[ColumnRef] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    limit: int | None = None
    descending: bool = True
    table_id: str | None = None
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregation": self.aggregation,
            "measure": self.measure.to_dict() if self.measure else None,
            "group_by": [g.to_dict() for g in self.group_by],
            "filters": [
                {"column": f.column.slug, "op": f.op, "value": f.value} for f in self.filters
            ],
            "time": self.time.to_dict(),
            "limit": self.limit,
            "assumptions": list(self.assumptions),
        }


def _to_ref(hit: LexHit) -> ColumnRef:
    return ColumnRef(
        table_id=hit.table_id,
        physical_name=hit.physical_name,
        slug=hit.column_slug or "",
        role=hit.role or hit.kind,
        display=hit.column_display or hit.display.split(" (")[0],
        unit=hit.unit,
    )


def _detect_aggregation(text: str) -> str | None:
    for operation, words in AGGREGATIONS.items():
        if any(f" {word} " in f" {text} " for word in words):
            return operation
    return None


def _fuzzy_rescue(lexicon: Lexicon, term: str, kinds: tuple[str, ...]) -> list[LexHit]:
    """FTS needs a token prefix match; misspellings need edit distance."""
    pool = lexicon.search(term[:3], kinds=kinds, limit=60) if len(term) >= 3 else []
    if not pool:
        return []
    scored = [
        (difflib.SequenceMatcher(None, term, (h.value or h.display).lower()).ratio(), h)
        for h in pool
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for ratio, hit in scored if ratio >= 0.72][:3]


def _distinct_by_column(hits: list[LexHit]) -> list[LexHit]:
    seen: set[tuple[str, str | None, str | None]] = set()
    out: list[LexHit] = []
    for hit in hits:
        if hit.key() not in seen:
            seen.add(hit.key())
            out.append(hit)
    return out


def _check_ambiguous(hits: list[LexHit], what: str, question: str) -> None:
    """Two near-equal referents in *different* columns is a question, not a guess."""
    if len(hits) < 2:
        return
    best, second = hits[0], hits[1]
    if (best.table_id, best.column_slug) == (second.table_id, second.column_slug):
        return
    if best.score <= 0 or (best.score - second.score) / best.score > AMBIGUITY_MARGIN:
        return
    raise AmbiguousQuery(
        f"More than one {what} matches this question.",
        remedy="Choose which one you meant, then ask again.",
        question=question,
        candidates=[
            # asdict(), not __dict__: Candidate uses slots and has no __dict__,
            # so this raised AttributeError and turned every 409 into a 500.
            asdict(Candidate(h.display, h.table_id, f"{h.physical_name}.{h.column_slug}"))
            for h in hits[:4]
        ],
    )


def _grouping_terms(slots: Slots, by_terms: list[str], before_by: str) -> list[str]:
    """Decide what the user wants grouped.

    English puts the grouping noun on either side of "by": "rainfall by station"
    but also "top 3 months by rainfall". When the by-clause names the measure we
    already resolved, the grouping noun is the text before it instead.
    """
    if not by_terms:
        # No "by" at all -- only a top-N implies a grouping.
        return [before_by] if slots.limit else []
    if slots.measure is None:
        return by_terms
    measure_tokens = set(tokenise(f"{slots.measure.slug} {slots.measure.display}"))
    remaining = [t for t in by_terms if not (set(tokenise(t)) & measure_tokens)]
    return remaining or [before_by]


def resolve(question: str, lexicon: Lexicon, table_hint: str | None = None) -> Slots:
    """Fill slots from a question.

    ``table_hint`` is how a disambiguation answer comes back: the caller passes
    the table the user picked and resolution is confined to it, so the same
    question cannot come back ambiguous a second time.
    """
    time_spec, residue = parse_time(question)
    slots = Slots(question=question, residue=residue, time=time_spec)
    slots.table_id = table_hint

    slots.aggregation = _detect_aggregation(residue)
    lowered = f" {residue} "
    slots.descending = not any(f" {word} " in lowered for word in ASCENDING)

    if match := _TOP_N.search(residue):
        slots.limit = int(match.group(1))
        slots.descending = any(w in match.group(0) for w in DESCENDING)

    group_terms: list[str] = []
    for match in _BY.finditer(residue):
        group_terms.append(match.group(1).strip())
    residue_wo_by = _BY.sub(" ", residue)

    # Search the whole residue, not the by-stripped text: "top 3 months by
    # rainfall" puts the measure inside the by-clause, and stripping it first
    # loses the only measure term in the question.
    measure_hits = _distinct_by_column(lexicon.search(residue, kinds=("measure",), limit=8))
    if table_hint:
        measure_hits = [h for h in measure_hits if h.table_id == table_hint] or measure_hits
    if not measure_hits:
        for token in tokenise(residue_wo_by):
            measure_hits = _distinct_by_column(_fuzzy_rescue(lexicon, token, ("measure",)))
            if measure_hits:
                slots.assumptions.append(f"Interpreted '{token}' as {measure_hits[0].display}.")
                break
    if measure_hits:
        if not table_hint:
            _check_ambiguous(measure_hits, "measure", question)
        slots.measure = _to_ref(measure_hits[0])
        slots.table_id = slots.measure.table_id

    grouping_terms = _grouping_terms(slots, group_terms, residue_wo_by)
    measure_slug = slots.measure.slug if slots.measure else None

    for term in grouping_terms:
        hits = [
            h
            for h in _distinct_by_column(
                lexicon.search(term, kinds=("dimension", "identifier", "time"), limit=6)
            )
            if h.column_slug != measure_slug
        ]
        preferred = [h for h in hits if slots.table_id in (None, h.table_id)] or hits
        if preferred:
            slots.group_by.append(_to_ref(preferred[0]))

    # One query for every value named in the question, not one per token.
    value_hits = [
        h for h in lexicon.match_values(residue_wo_by) if slots.table_id in (None, h.table_id)
    ]
    if not value_hits:
        for token in tokenise(residue_wo_by):
            if len(token) < 4 or token in AGGREGATIONS:
                continue
            rescued = [
                h
                for h in _fuzzy_rescue(lexicon, token, ("value",))
                if slots.table_id in (None, h.table_id)
            ]
            if rescued:
                slots.assumptions.append(
                    f"Matched '{token}' to {rescued[0].value} by closest spelling."
                )
                value_hits = rescued
                break

    for hit in value_hits:
        column = _to_ref(hit)
        if any(f.column.slug == column.slug for f in slots.filters):
            continue
        slots.filters.append(Filter(column=column, op="=", value=hit.value))
        if slots.table_id is None:
            slots.table_id = hit.table_id

    for match in _COMPARATOR.finditer(residue_wo_by):
        if slots.measure is None:
            break
        slots.filters.append(
            Filter(
                column=slots.measure,
                op=_COMPARATOR_OPS[match.group(1)],
                value=float(match.group(2)),
            )
        )

    if slots.measure is not None and slots.aggregation is None:
        slots.aggregation = _default_aggregation(slots.measure)
        word = "averaged" if slots.aggregation == "avg" else "totalled"
        slots.assumptions.append(f"No aggregation named; {word} the matching rows.")

    _note_partial_match(slots, residue_wo_by)
    return slots


#: Measures that must never be summed by default. Adding twelve monthly mean
#: temperatures produces a number with no physical meaning, and it looks
#: plausible enough that nobody questions it.
_MEAN_UNITS = frozenset({"degC", "percent", "hPa", "kmph"})
_MEAN_TOKENS = ("temp", "humidity", "pressure", "uptime", "percent", "ratio", "index")


def _default_aggregation(measure: ColumnRef) -> str:
    if measure.unit in _MEAN_UNITS:
        return "avg"
    if any(token in measure.slug for token in _MEAN_TOKENS):
        return "avg"
    return "sum"


def _note_partial_match(slots: Slots, residue: str) -> None:
    """Say so when the question was only partly matched.

    "Sea surface temperature" resolving to "Max Temp (C)" is a defensible guess
    and a dangerous silent one -- SST is not air temperature. Tokens already
    accounted for by a filter or a grouping are not evidence of a mismatch, so
    they are subtracted first.
    """
    if slots.measure is None:
        return

    accounted = set(tokenise(slots.measure.display))
    for filter_ in slots.filters:
        accounted |= set(tokenise(f"{filter_.value} {filter_.column.display}"))
    for group in slots.group_by:
        accounted |= set(tokenise(group.display))

    # A grouping on "month" explains the word "months"; treat a trailing s as
    # the same token rather than as an unmatched concept.
    accounted |= {t + "s" for t in accounted} | {t[:-1] for t in accounted if len(t) > 3}

    def explained(token: str) -> bool:
        # "temp" explains "temperature"; a shared four-character stem is enough.
        return any(
            token.startswith(known[:4]) or known.startswith(token[:4])
            for known in accounted
            if len(known) >= 4 and len(token) >= 4
        )

    unexplained = {
        token
        for token in set(tokenise(residue)) - _STOPWORDS - accounted
        if not token.isdigit() and not explained(token)
    }
    if not unexplained:
        return

    phrase = " ".join(token for token in tokenise(residue) if token not in _STOPWORDS)
    slots.assumptions.insert(0, f"Interpreted '{phrase}' as {slots.measure.display}.")
