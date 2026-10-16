"""Ruff, with grids: brackets whose first line holds several items keep them packed.

    s = [
        1, 2, 3,
        4, 5, 6,
    ]
    bfs(
        n, m, k,
        start=(sr, sc),
        blocked=walls,
    )

If the first line inside a split bracket holds several items, later lines are packed up to its width
instead of one item per line. The first line may also start right after the bracket (`s = [1, 2, 3,`).
Like Ruff's magic trailing comma, it all needs a trailing comma after the last item. Ruff has no
bin-packing option, but it keeps a comment that follows an opening bracket on that line: `grid` tags
each grid with `# __grid:<k>` before Ruff runs, and `ungrid` repacks to the width of the first k items
as Ruff wrote them, so a second format changes nothing. Ruff's import sorting writes a parenthesized
import one name per line; `regrid_imports` packs a `from m import (` that was a grid again.

The grid code is copied from byname's fmt.py (byname isn't a dependency).
"""

import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ORGANIZE = ["--select", "I001"]  # what the Ruff extension's source.organizeImports applies


class FormatError(Exception):
    pass


class UnparsableError(FormatError):
    """Ruff couldn't parse the code (e.g. a half-typed line): nothing to format, not a failure."""


def resolve_ruff(root: Path | None = None) -> str:
    """The project's ruff (<root>/.venv/bin/ruff), else the one installed beside ruffgrid, else PATH's."""
    for d in ([root / ".venv" / "bin"] if root else []) + [Path(sys.executable).parent]:
        if (d / "ruff").exists():
            return str(d / "ruff")
    return "ruff"


GRID = "# __grid:"  # + how many items the first line holds
GRID_RE = re.compile(r"# __grid:(\d+)")


def _pairs(toks: list[tokenize.TokenInfo]) -> dict[int, int]:
    pair: dict[int, int] = {}
    stack: list[int] = []
    for i, t in enumerate(toks):
        if t.type == tokenize.OP and t.string in "([{":
            stack.append(i)
        elif t.type == tokenize.OP and t.string in ")]}" and stack:
            pair[stack.pop()] = i
    return pair


def _items(toks: list[tokenize.TokenInfo], o: int, c: int, pair: dict[int, int]) -> list[tuple[int, int]] | None:
    """Top-level items between brackets o and c as (first token, its comma), or None if a comment
    sits inside or the last item has no trailing comma."""
    items: list[tuple[int, int]] = []
    first, j = None, o + 1
    while j < c:
        t = toks[j]
        if t.type == tokenize.COMMENT:
            return None
        if t.type not in (tokenize.NL, tokenize.NEWLINE):
            if first is None:
                first = j
            if j in pair:
                j = pair[j]
            elif t.string == ",":
                items.append((first, j))
                first = None
        j += 1
    return items if items and first is None else None


def _grids(src: str) -> tuple[list[tokenize.TokenInfo], list[tuple[int, int, bool]]] | None:
    """src's tokens and its grid-shaped brackets as (opening token, items on the first line, whether
    the first line starts right after the bracket). Anything under `# fmt: off`, or in a statement
    marked `# fmt: skip`, is left out. None if src doesn't tokenize."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return None  # broken code: ruff reports it
    pair = _pairs(toks)
    skip: set[int] = set()
    off, stmt = False, 0
    for i, t in enumerate(toks):
        if t.type == tokenize.COMMENT:
            text = t.string.replace(" ", "")
            off = {"#fmt:off": True, "#fmt:on": False}.get(text, off)
            if text == "#fmt:skip":
                skip.update(range(stmt, i))
        if off:
            skip.add(i)
        if t.type == tokenize.NEWLINE:
            stmt = i + 1
    grids: list[tuple[int, int, bool]] = []
    for o, c in pair.items():
        if o in skip or toks[c].start[0] == toks[o].start[0]:
            continue
        items = _items(toks, o, c, pair)
        if not items:
            continue
        hug = toks[o + 1].type != tokenize.NL  # `[1, 2, 3,` : the first line starts right after the bracket
        row = toks[items[0][0]].start[0]
        k = sum(1 for _, b in items if toks[b].end[0] == row)
        if k >= 2 and toks[items[k - 1][1] + 1].type == tokenize.NL:  # k whole items, nothing after
            grids.append((o, k, hug))
    return toks, grids


def _tag(src: str, toks: list[tokenize.TokenInfo], tags: list[tuple[int, int, bool]]) -> str:
    """src with `# __grid:k` after each (opening token, k, hug) bracket; a hugged first line moves below the tag."""
    starts = [0]
    for line in src.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    inserts = []
    for o, k, hug in tags:
        r, col = toks[o].end
        inserts.append((starts[r - 1] + col, f"  {GRID}{k}" + "\n" * hug))
    out = src
    for at, text in sorted(inserts, reverse=True):
        out = out[:at] + text + out[at:]
    return out


def grid(src: str) -> str:
    """Tag each grid-shaped bracket with a GRID comment (see the module docstring)."""
    if (found := _grids(src)) is None:
        return src
    return _tag(src, *found)


def _import_from(toks: list[tokenize.TokenInfo], o: int) -> str | None:
    """The module of `from <module> import (`, if toks[o] is that bracket."""
    if o < 2 or toks[o - 1].string != "import":
        return None
    j = o - 2
    while j >= 0 and toks[j].string != "from":
        if toks[j].type != tokenize.NAME and toks[j].string not in (".", "..."):
            return None
        j -= 1
    return "".join(t.string for t in toks[j + 1 : o - 1]) if j >= 0 else None


def regrid_imports(before: str, after: str) -> str:
    """after (before with Ruff's import sorting applied, which writes one name per line) with each
    `from m import (` that was a grid in before packed again, keeping its first line's item count."""
    if (old := _grids(before)) is None:
        return after
    toks, grids = old
    counts: dict[str, int] = {}
    for o, k, _ in grids:
        if (m := _import_from(toks, o)) is not None:
            counts.setdefault(m, k)
    if not counts:
        return after
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(after).readline))
    except (tokenize.TokenError, SyntaxError):
        return after
    pair = _pairs(toks)
    tags = [
        (o, counts[m], False)
        for o, c in pair.items()
        if toks[c].start[0] != toks[o].start[0]
        and (m := _import_from(toks, o)) in counts
        and toks[o + 1].type == tokenize.NL
    ]
    return ungrid(_tag(after, toks, tags)) if tags else after


def ungrid(code: str) -> str:
    """Repack each GRID-tagged bracket that ruff split one item per line, and drop the tags."""
    toks = list(tokenize.generate_tokens(io.StringIO(code).readline))
    pair = _pairs(toks)
    lines = code.splitlines(keepends=True)
    edits: list[tuple[int, int, list[str]]] = []  # lines[a:b] = new
    for i, t in enumerate(toks):
        m = GRID_RE.fullmatch(t.string) if t.type == tokenize.COMMENT else None
        if not m:
            continue
        r = t.start[0] - 1
        lines[r] = lines[r][: t.start[1]].rstrip(" \t") + lines[r][t.end[1] :]
        o = i - 1
        if o not in pair:
            continue
        toks[i] = t._replace(type=tokenize.NL)  # the tag isn't a comment inside the bracket
        items = _items(toks, o, pair[o], pair) or []
        rows = [toks[a].start[0] for a, _ in items]
        if not items or len(set(rows)) != len(rows) or any(toks[b].end[0] != rr for (_, b), rr in zip(items, rows)):
            continue  # not one item per line (an item spans lines): leave it as ruff wrote it
        texts = [lines[rr - 1][toks[a].start[1] : toks[b].start[1]] for (a, b), rr in zip(items, rows)]
        indent = lines[rows[0] - 1][: toks[items[0][0]].start[1]]
        width = len(", ".join(texts[: int(m.group(1))]))
        packed: list[list[str]] = [[]]
        for s in texts:
            if packed[-1] and len(", ".join([*packed[-1], s])) > width:
                packed.append([])
            packed[-1].append(s)
        edits.append((rows[0] - 1, rows[-1], [indent + ", ".join(p) + ",\n" for p in packed]))
    for a, b, new in sorted(edits, reverse=True):
        lines[a:b] = new
    return "".join(lines)


TAG_RE = re.compile(r"[ \t]*# __grid:\d+")


def _ungrid(code: str) -> str:
    try:
        return ungrid(code)
    except (tokenize.TokenError, SyntaxError):  # e.g. a cell magic Python can't tokenize: Ruff's layout
        return TAG_RE.sub("", code)


def _run(ruff: str, args: list[str], path: Path, text: str) -> subprocess.CompletedProcess:
    """ruff <args> on text as if it were the file at path (Ruff finds the project's config from it). A file
    the config excludes comes back as it is, as in the Ruff extension (--force-exclude)."""
    path = path.absolute()  # it runs in path's folder
    return subprocess.run(
        [ruff, *args, "--force-exclude", "--stdin-filename", str(path), "-"],
        input=text,
        capture_output=True,
        text=True,
        cwd=path.parent if path.parent.is_dir() else None,
        check=False,
    )


def _notebook(cells: list[str]) -> str:
    return json.dumps(
        {
            "cells": [
                {"cell_type": "code", "metadata": {}, "source": c, "outputs": [], "execution_count": None}
                for c in cells
            ],
            "metadata": {"language_info": {"name": "python"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


def _cells(out: str, original: list[str]) -> list[str]:
    """The code cells' sources from Ruff's notebook output. A cell's last line keeps having no newline."""
    srcs = ["".join(c["source"]) if isinstance(c["source"], list) else c["source"] for c in json.loads(out)["cells"]]
    return [c if o.endswith("\n") else c.removesuffix("\n") for c, o in zip(srcs, original)]


def format_py(src: str, path: Path, ruff: str = "ruff") -> str:
    """src formatted by Ruff, grids kept. path: the file (or where it would be), for the project's config."""
    p = _run(ruff, ["format"], path.with_suffix(".py"), tagged := grid(src))
    if p.returncode != 0:
        err = p.stderr.strip()
        raise (UnparsableError if err.startswith("error: Failed to parse") else FormatError)(
            err or "ruff format failed"
        )
    # unchanged: formatted already, or a file the config excludes (a hugged first line moved below its
    # tag never comes back unchanged, so src it is)
    return src if p.stdout == tagged else _ungrid(p.stdout)


def format_cells(cells: list[str], path: Path, ruff: str = "ruff") -> list[str]:
    """A notebook's code cells formatted by Ruff as notebook cells (magics are fine), grids kept. A cell
    Ruff can't parse is left as it is; the others are still formatted."""
    tagged = [grid(c) for c in cells]
    p = _run(ruff, ["format"], path.with_suffix(".ipynb"), _notebook(tagged))
    if p.returncode == 0:  # a cell Ruff left as it was is the cell as written (see format_py)
        return [o if c == t else _ungrid(c) for c, t, o in zip(_cells(p.stdout, tagged), tagged, cells)]
    if len(cells) < 2:
        if p.stderr.strip().startswith("error: Failed to parse"):
            return cells
        raise FormatError(p.stderr.strip() or "ruff format failed")
    return [format_cells([c], path, ruff)[0] for c in cells]  # find the broken one: format each alone


def fix_py(src: str, path: Path, ruff: str = "ruff", select: list[str] | None = None) -> str:
    """src with Ruff's fixes applied (`ruff check --fix`), import grids kept. select: e.g. ORGANIZE."""
    p = _run(ruff, ["check", "--fix-only", "--exit-zero", *(select or [])], path.with_suffix(".py"), src)
    if p.returncode != 0:
        raise FormatError(p.stderr.strip() or "ruff check failed")
    return regrid_imports(src, p.stdout) if p.stdout != src else src


def fix_cells(cells: list[str], path: Path, ruff: str = "ruff", select: list[str] | None = None) -> list[str]:
    """A notebook's code cells with Ruff's fixes applied, Ruff seeing the whole notebook: an import used
    in a later cell isn't unused. Import grids kept."""
    args = ["check", "--fix-only", "--exit-zero", *(select or [])]
    p = _run(ruff, args, path.with_suffix(".ipynb"), _notebook(cells))
    if p.returncode != 0:
        raise FormatError(p.stderr.strip() or "ruff check failed")
    try:
        fixed = _cells(p.stdout, cells)
    except (ValueError, KeyError):  # a cell ruff can't parse: it writes nothing back
        return cells
    return [regrid_imports(o, c) if c != o else c for c, o in zip(fixed, cells)]
