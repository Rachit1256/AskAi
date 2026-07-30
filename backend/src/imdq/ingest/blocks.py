"""The unit of ingestion: one rectangular region of a worksheet."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BlockKind(StrEnum):
    TABLE = "table"
    CROSSTAB = "crosstab"
    KEY_VALUE = "key_value"
    BANNER = "banner"
    FRAGMENT = "fragment"


def column_letter(index: int) -> str:
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


@dataclass(slots=True)
class Block:
    r0: int
    r1: int
    c0: int
    c1: int
    kind: BlockKind = BlockKind.FRAGMENT
    header_rows: list[int] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    context: dict[str, object] = field(default_factory=dict)
    totals_row: int | None = None
    confidence: float = 0.0

    @property
    def height(self) -> int:
        return self.r1 - self.r0 + 1

    @property
    def width(self) -> int:
        return self.c1 - self.c0 + 1

    @property
    def body_start(self) -> int:
        return self.header_rows[-1] + 1 if self.header_rows else self.r0

    @property
    def a1(self) -> str:
        return (
            f"{column_letter(self.c0)}{self.r0 + 1}:"
            f"{column_letter(self.c1)}{self.r1 + 1}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "range": self.a1,
            "kind": str(self.kind),
            "rows": self.height,
            "columns": self.width,
            "header": list(self.header),
            "context": dict(self.context),
            "totals_row": (self.totals_row + 1) if self.totals_row is not None else None,
            "confidence": round(self.confidence, 3),
        }
