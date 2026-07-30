"""Autonomous discovery of sub-tables inside a worksheet.

No templates and no per-file configuration: the segmenter reads geometry and
styling and decides where each block starts and stops. Stages, in order:

1. Banner peeling    -- merged or single-cell text rows are lifted out first,
                        otherwise a full-width title vetoes every column cut.
2. Recursive XY-cut  -- split on blank row/column runs, widest gap first.
3. Components        -- 8-connected fallback for interlocked regions XY-cut
                        cannot divide.
4. Stacked split     -- a mid-region header row starts a new block even with no
                        blank row between the two tables.
5. Header detection  -- scored against the modal dtype signature of the body.
6. Classification    -- table / crosstab / key_value / banner / fragment.
7. Context lifting   -- sheet-level and local scopes, with a barrier rule.
"""

from __future__ import annotations

from collections import Counter, deque

import numpy as np

from imdq.ingest.blocks import Block, BlockKind
from imdq.ingest.grid import DATE, EMPTY, NUM, TEXT, Grid

MAX_CUT_DEPTH = 10
HEADER_SCAN_ROWS = 4
MIN_TABLE_ROWS = 3
HEADER_ACCEPT = 0.45
HIERARCHICAL_HEADER_ACCEPT = 0.62
STACKED_HEADER_ACCEPT = 0.70
TOTALS_TOKENS = ("total", "grand total", "subtotal", "sum", "overall")


# ---------------------------------------------------------------- banners


def _banner_rows(grid: Grid) -> np.ndarray:
    """Text-only rows that are a single cell or a wide merge: titles and notes.

    Exactly two occupied cells is a key/value row, not a title -- treating those
    as banners shreds the context block that qualifies every table below it.
    """
    flags = np.zeros(grid.n_rows, dtype=bool)
    for r in range(grid.n_rows):
        occupied = int(grid.occ[r].sum())
        if occupied == 0:
            continue
        if int((grid.dtype[r] == TEXT).sum()) != occupied:
            continue
        if grid.merged_rows[r] or occupied == 1:
            flags[r] = True
    return flags


# ---------------------------------------------------------------- XY-cut


def _interior_runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(mask):
        if value and start is None:
            start = i
        elif not value and start is not None:
            if i - start >= min_len and start != 0:
                runs.append((start, i - start))
            start = None
    return runs


def _trim(occ: np.ndarray, block: Block) -> Block | None:
    """Shrink to the occupied extent. Always pass the *peeled* occupancy."""
    sub = occ[block.r0 : block.r1 + 1, block.c0 : block.c1 + 1]
    if not sub.any():
        return None
    rows = np.flatnonzero(sub.any(axis=1))
    cols = np.flatnonzero(sub.any(axis=0))
    return Block(
        block.r0 + int(rows[0]),
        block.r0 + int(rows[-1]),
        block.c0 + int(cols[0]),
        block.c0 + int(cols[-1]),
    )


def _xy_cut(
    occ: np.ndarray,
    block: Block,
    min_row_gap: int,
    min_col_gap: int,
    depth: int = 0,
) -> list[Block]:
    trimmed = _trim(occ, block)
    if trimmed is None:
        return []
    if depth >= MAX_CUT_DEPTH:
        return [trimmed]

    sub = occ[trimmed.r0 : trimmed.r1 + 1, trimmed.c0 : trimmed.c1 + 1]
    row_gaps = _interior_runs(~sub.any(axis=1), min_row_gap)
    col_gaps = _interior_runs(~sub.any(axis=0), min_col_gap)
    widest_row = max((n for _, n in row_gaps), default=0)
    widest_col = max((n for _, n in col_gaps), default=0)
    if widest_row == 0 and widest_col == 0:
        return [trimmed]

    out: list[Block] = []
    cut_rows = widest_row >= widest_col  # ties favour rows
    gaps = row_gaps if cut_rows else col_gaps
    extent = trimmed.height if cut_rows else trimmed.width
    bounds = [(s, s + n) for s, n in gaps] + [(extent, extent)]

    prev = 0
    for start, end in bounds:
        if start > prev:
            piece = (
                Block(trimmed.r0 + prev, trimmed.r0 + start - 1, trimmed.c0, trimmed.c1)
                if cut_rows
                else Block(trimmed.r0, trimmed.r1, trimmed.c0 + prev, trimmed.c0 + start - 1)
            )
            out.extend(_xy_cut(occ, piece, min_row_gap, min_col_gap, depth + 1))
        prev = end
    return out


def _components(occ: np.ndarray, block: Block, bridge: int = 1) -> list[Block]:
    """8-connected labelling after closing gaps of ``bridge`` cells."""
    sub = occ[block.r0 : block.r1 + 1, block.c0 : block.c1 + 1]
    height, width = sub.shape
    dilated = sub.copy()
    for _ in range(bridge):
        grown = dilated.copy()
        grown[1:, :] |= dilated[:-1, :]
        grown[:-1, :] |= dilated[1:, :]
        grown[:, 1:] |= dilated[:, :-1]
        grown[:, :-1] |= dilated[:, 1:]
        dilated = grown

    seen = np.zeros_like(dilated)
    found: list[Block] = []
    for i in range(height):
        for j in range(width):
            if not dilated[i, j] or seen[i, j]:
                continue
            queue = deque([(i, j)])
            seen[i, j] = True
            cells: list[tuple[int, int]] = []
            while queue:
                y, x = queue.popleft()
                if sub[y, x]:
                    cells.append((y, x))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if (
                            0 <= ny < height
                            and 0 <= nx < width
                            and dilated[ny, nx]
                            and not seen[ny, nx]
                        ):
                            seen[ny, nx] = True
                            queue.append((ny, nx))
            if cells:
                ys = [c[0] for c in cells]
                xs = [c[1] for c in cells]
                found.append(
                    Block(
                        block.r0 + min(ys),
                        block.r0 + max(ys),
                        block.c0 + min(xs),
                        block.c0 + max(xs),
                    )
                )
    return found or [block]


# ---------------------------------------------------------------- headers


def _modal_body_signature(grid: Grid, r_from: int, r_to: int, c0: int, c1: int) -> np.ndarray:
    rows = range(r_from, min(r_to, r_from + 8) + 1)
    signature = np.zeros(c1 - c0 + 1, dtype=np.uint8)
    for k, c in enumerate(range(c0, c1 + 1)):
        seen = [int(grid.dtype[r, c]) for r in rows if grid.dtype[r, c] != EMPTY]
        signature[k] = Counter(seen).most_common(1)[0][0] if seen else EMPTY
    return signature


def _header_score(grid: Grid, row: int, body_sig: np.ndarray, c0: int, c1: int) -> float:
    width = c1 - c0 + 1
    row_types = grid.dtype[row, c0 : c1 + 1]
    occupied = int((row_types != EMPTY).sum())
    if occupied == 0:
        return 0.0

    text_ratio = float((row_types == TEXT).sum()) / occupied
    diverging = sum(
        1
        for k in range(width)
        if body_sig[k] != EMPTY and row_types[k] != EMPTY and row_types[k] != body_sig[k]
    )
    divergence = diverging / width
    styled = (
        1.0 if (grid.bold[row, c0 : c1 + 1].any() or grid.filled[row, c0 : c1 + 1].any()) else 0.0
    )
    labels = [
        str(grid.values[row, c]).strip() for c in range(c0, c1 + 1) if grid.dtype[row, c] != EMPTY
    ]
    uniqueness = len(set(labels)) / max(1, len(labels))
    density = occupied / width

    return (
        0.34 * text_ratio + 0.28 * divergence + 0.16 * styled + 0.10 * uniqueness + 0.12 * density
    )


def _detect_header(grid: Grid, block: Block) -> tuple[list[int], float]:
    if block.height < 2:
        return [], 0.0
    best_row, best_score = block.r0, 0.0
    for row in range(block.r0, min(block.r0 + HEADER_SCAN_ROWS, block.r1)):
        signature = _modal_body_signature(grid, row + 1, block.r1, block.c0, block.c1)
        score = _header_score(grid, row, signature, block.c0, block.c1)
        if score > best_score:
            best_row, best_score = row, score

    rows = [best_row]
    if best_row + 1 < block.r1:
        signature = _modal_body_signature(grid, best_row + 2, block.r1, block.c0, block.c1)
        if (
            _header_score(grid, best_row + 1, signature, block.c0, block.c1)
            > HIERARCHICAL_HEADER_ACCEPT
        ):
            rows.append(best_row + 1)
    return rows, best_score


def _header_labels(grid: Grid, block: Block) -> list[str]:
    labels: list[str] = []
    for c in range(block.c0, block.c1 + 1):
        parts = [
            str(grid.values[r, c]).strip() for r in block.header_rows if grid.dtype[r, c] != EMPTY
        ]
        labels.append(" / ".join(dict.fromkeys(parts)) if parts else f"col_{c}")
    return labels


def _find_totals_row(grid: Grid, block: Block) -> None:
    for r in range(block.r1, block.body_start - 1, -1):
        for c in range(block.c0, min(block.c0 + 2, block.c1 + 1)):
            value = grid.values[r, c]
            if isinstance(value, str) and any(t in value.lower() for t in TOTALS_TOKENS):
                block.totals_row = r
                return


# ---------------------------------------------------------------- classify


def _classify(grid: Grid, block: Block) -> None:
    if block.height == 1:
        block.kind, block.confidence = BlockKind.BANNER, 0.9
        return

    types = grid.dtype[block.r0 : block.r1 + 1, block.c0 : block.c1 + 1]
    if int((types != EMPTY).sum()) == 0:
        block.kind, block.confidence = BlockKind.FRAGMENT, 0.0
        return

    block.header_rows, header_confidence = _detect_header(grid, block)
    block.header = _header_labels(grid, block)

    # key/value vs a narrow two-column table (Reason | Minutes). The decisive
    # signal is the right-hand column: a table's values are homogeneous, a
    # key/value block's are not. A styled first row means a real header.
    if block.width == 2 and block.height >= 2:
        left_text = float((grid.dtype[block.r0 : block.r1 + 1, block.c0] == TEXT).mean())
        right_occupied = float((grid.dtype[block.r0 : block.r1 + 1, block.c1] != EMPTY).mean())
        right_types = {int(t) for t in grid.dtype[block.r0 : block.r1 + 1, block.c1] if t != EMPTY}
        styled_header = bool(
            grid.bold[block.r0, block.c0 : block.c1 + 1].any()
            or grid.filled[block.r0, block.c0 : block.c1 + 1].any()
        )
        looks_like_key_value = (
            left_text >= 0.8
            and right_occupied >= 0.6
            and len(right_types) > 1
            and not styled_header
        )
        if looks_like_key_value:
            block.kind = BlockKind.KEY_VALUE
            block.confidence = 0.75 + 0.2 * left_text
            block.header_rows, block.header = [], []
            return

    if block.width >= 3 and block.height >= 3 and block.header_rows:
        header_types = {
            int(t)
            for t in grid.dtype[block.header_rows[0], block.c0 + 1 : block.c1 + 1]
            if t != EMPTY
        }
        interior = grid.dtype[block.body_start : block.r1 + 1, block.c0 + 1 : block.c1 + 1]
        numeric_ratio = float((interior == NUM).sum()) / max(1, int((interior != EMPTY).sum()))
        first_col_text = float(
            (grid.dtype[block.body_start : block.r1 + 1, block.c0] == TEXT).mean()
        )
        if (
            header_types
            and header_types <= {DATE, NUM}
            and numeric_ratio >= 0.75
            and first_col_text >= 0.7
        ):
            block.kind = BlockKind.CROSSTAB
            block.confidence = 0.6 + 0.3 * numeric_ratio
            return

    if block.height >= MIN_TABLE_ROWS and block.width >= 2 and header_confidence >= HEADER_ACCEPT:
        block.kind = BlockKind.TABLE
        block.confidence = min(0.99, header_confidence + 0.15)
        _find_totals_row(grid, block)
        return

    block.kind, block.confidence = BlockKind.FRAGMENT, 0.3


def _split_stacked(grid: Grid, block: Block, min_body: int = 2) -> list[Block]:
    if block.height < 6:
        return [block]
    cuts: list[int] = []
    for row in range(block.r0 + 2, block.r1 - min_body + 1):
        below = _modal_body_signature(grid, row + 1, block.r1, block.c0, block.c1)
        here = _header_score(grid, row, below, block.c0, block.c1)
        above = _modal_body_signature(grid, row, block.r1, block.c0, block.c1)
        prior = _header_score(grid, row - 1, above, block.c0, block.c1)
        if here >= STACKED_HEADER_ACCEPT and prior < 0.55:
            cuts.append(row)
    if not cuts:
        return [block]

    pieces: list[Block] = []
    start = block.r0
    for row in [*cuts, block.r1 + 1]:
        if row - start >= 2:
            pieces.append(Block(start, row - 1, block.c0, block.c1))
        start = row
    return pieces or [block]


# ---------------------------------------------------------------- context


def _key_value_pairs(grid: Grid, block: Block) -> dict[str, object]:
    pairs: dict[str, object] = {}
    for r in range(block.r0, block.r1 + 1):
        key = grid.values[r, block.c0]
        value = grid.values[r, block.c1]
        if isinstance(key, str) and key.strip() and value is not None:
            slug = key.strip().rstrip(":").lower().replace(" ", "_")
            pairs[slug] = value
    return pairs


def _donor_pairs(grid: Grid, block: Block) -> dict[str, object]:
    if block.kind is BlockKind.KEY_VALUE:
        return _key_value_pairs(grid, block)
    text = next(
        (
            str(grid.values[block.r0, c]).strip()
            for c in range(block.c0, block.c1 + 1)
            if grid.dtype[block.r0, c] != EMPTY
        ),
        None,
    )
    return {"_section": text} if text else {}


def _attach_context(grid: Grid, blocks: list[Block]) -> None:
    """Two scopes, deliberately distinct.

    ``sheet`` -- donors above the first table, applied to every block.
    ``local`` -- donors between this block and the nearest preceding table that
    overlaps its columns. Conflating the two leaks one table's key/value rows
    into an unrelated table further down the sheet.
    """
    donors = [b for b in blocks if b.kind in (BlockKind.KEY_VALUE, BlockKind.BANNER)]
    tables = [b for b in blocks if b.kind in (BlockKind.TABLE, BlockKind.CROSSTAB)]
    if not tables:
        return
    first_table_row = min(t.r0 for t in tables)

    sheet_context: dict[str, object] = {}
    for donor in sorted(
        (d for d in donors if d.r1 < first_table_row), key=lambda d: d.r1, reverse=True
    ):
        for key, value in _donor_pairs(grid, donor).items():
            sheet_context.setdefault(key, value)

    for table in tables:
        barrier = max(
            (
                t.r1
                for t in tables
                if t is not table and t.r1 < table.r0 and not (t.c1 < table.c0 or t.c0 > table.c1)
            ),
            default=-1,
        )
        local: dict[str, object] = {}
        candidates = (
            d
            for d in donors
            if barrier < d.r1 < table.r0
            and d.r1 >= first_table_row
            and not (d.c1 < table.c0 or d.c0 > table.c1)
        )
        for donor in sorted(candidates, key=lambda d: d.r1, reverse=True):
            for key, value in _donor_pairs(grid, donor).items():
                local.setdefault(key, value)
        table.context = {**sheet_context, **local}


# ---------------------------------------------------------------- entry


def segment(grid: Grid, min_row_gap: int = 1, min_col_gap: int = 1) -> list[Block]:
    if grid.n_rows == 0:
        return []

    banners = _banner_rows(grid)
    peeled = grid.occ.copy()
    peeled[banners] = False

    root = Block(0, grid.n_rows - 1, 0, grid.n_cols - 1)
    regions = _xy_cut(peeled, root, min_row_gap, min_col_gap)

    refined: list[Block] = []
    for region in regions:
        parts = _components(peeled, region) if region.height > 3 and region.width > 3 else [region]
        for part in parts:
            trimmed = _trim(peeled, part)
            if trimmed is not None:
                refined.extend(_split_stacked(grid, trimmed))

    blocks = [b for b in (_trim(peeled, r) for r in refined) if b is not None]
    for block in blocks:
        _classify(grid, block)

    for row in np.flatnonzero(banners):
        cols = np.flatnonzero(grid.occ[row])
        if cols.size:
            blocks.append(
                Block(
                    int(row),
                    int(row),
                    int(cols[0]),
                    int(cols[-1]),
                    kind=BlockKind.BANNER,
                    confidence=0.9,
                )
            )

    blocks.sort(key=lambda b: (b.r0, b.c0))
    _attach_context(grid, blocks)
    return [b for b in blocks if b.kind is not BlockKind.FRAGMENT or b.height * b.width > 4]
