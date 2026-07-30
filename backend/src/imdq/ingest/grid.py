"""Cell feature planes for one worksheet.

Layout detection needs cell *types* and *styling*, not values, so the grid is
built from a bounded probe window rather than the whole sheet. A 400k-row
station archive is segmented from its first 2000 rows in milliseconds; the body
is streamed later by the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import numpy as np

EMPTY: Final[int] = 0
TEXT: Final[int] = 1
NUM: Final[int] = 2
DATE: Final[int] = 3
BOOL: Final[int] = 4

DTYPE_NAMES: Final[dict[int, str]] = {
    EMPTY: "empty", TEXT: "text", NUM: "num", DATE: "date", BOOL: "bool"
}

_NUM_RE = re.compile(r"^[(\-+]?\s*[\u20b9$\u20ac\u00a3\u00a5]?\s*\d[\d,\s]*(\.\d+)?\s*%?\s*\)?$")

DEFAULT_PROBE_ROWS: Final[int] = 2_000
MAX_COLS: Final[int] = 512


def cell_dtype(value: Any) -> int:
    if value is None:
        return EMPTY
    if isinstance(value, bool):
        return BOOL
    if isinstance(value, (datetime, date)):
        return DATE
    if isinstance(value, (int, float)):
        return NUM
    text = str(value).strip()
    if not text:
        return EMPTY
    return NUM if _NUM_RE.match(text) else TEXT


@dataclass(slots=True)
class Grid:
    """Feature planes over the probe window. Indices are 0-based."""

    values: np.ndarray
    dtype: np.ndarray
    occ: np.ndarray
    bold: np.ndarray
    filled: np.ndarray
    merged_rows: np.ndarray
    truncated: bool
    sheet_name: str

    @property
    def n_rows(self) -> int:
        return int(self.occ.shape[0])

    @property
    def n_cols(self) -> int:
        return int(self.occ.shape[1])

    @classmethod
    def empty(cls, sheet_name: str = "") -> Grid:
        zeros_2d = np.zeros((0, 0), dtype=bool)
        return cls(
            values=np.empty((0, 0), dtype=object),
            dtype=zeros_2d.astype(np.uint8),
            occ=zeros_2d,
            bold=zeros_2d,
            filled=zeros_2d,
            merged_rows=np.zeros(0, dtype=bool),
            truncated=False,
            sheet_name=sheet_name,
        )

    @classmethod
    def from_worksheet(cls, worksheet: Any, probe_rows: int = DEFAULT_PROBE_ROWS) -> Grid:
        declared_rows = worksheet.max_row or 0
        n_rows = min(declared_rows, probe_rows)
        n_cols = min(worksheet.max_column or 0, MAX_COLS)
        if n_rows == 0 or n_cols == 0:
            return cls.empty(worksheet.title)

        values = np.empty((n_rows, n_cols), dtype=object)
        dtype = np.zeros((n_rows, n_cols), dtype=np.uint8)
        bold = np.zeros((n_rows, n_cols), dtype=bool)
        filled = np.zeros((n_rows, n_cols), dtype=bool)

        rows = worksheet.iter_rows(min_row=1, max_row=n_rows, min_col=1, max_col=n_cols)
        for row in rows:
            for cell in row:
                i, j = cell.row - 1, cell.column - 1
                values[i, j] = cell.value
                dtype[i, j] = cell_dtype(cell.value)
                font = getattr(cell, "font", None)
                if font is not None and font.bold:
                    bold[i, j] = True
                fill = getattr(cell, "fill", None)
                fg = getattr(fill, "fgColor", None) if fill is not None else None
                rgb = getattr(fg, "rgb", None)
                if isinstance(rgb, str) and rgb not in ("00000000", "FFFFFFFF"):
                    filled[i, j] = True

        merged_rows = np.zeros(n_rows, dtype=bool)
        ranges = getattr(getattr(worksheet, "merged_cells", None), "ranges", [])
        for rng in ranges:
            r0, c0 = rng.min_row - 1, rng.min_col - 1
            r1, c1 = min(rng.max_row, n_rows) - 1, min(rng.max_col, n_cols) - 1
            if r0 < 0 or c0 < 0 or r1 < r0 or c1 < c0:
                continue
            anchor = dtype[r0, c0]
            if anchor != EMPTY:
                # Occupy the whole span: a merged header cell would otherwise
                # manufacture a blank column and trigger a bogus vertical cut.
                dtype[r0 : r1 + 1, c0 : c1 + 1] = anchor
            if (c1 - c0) >= 2:
                merged_rows[r0 : r1 + 1] = True

        return cls(
            values=values,
            dtype=dtype,
            occ=dtype != EMPTY,
            bold=bold,
            filled=filled,
            merged_rows=merged_rows,
            truncated=declared_rows > n_rows,
            sheet_name=worksheet.title,
        )
