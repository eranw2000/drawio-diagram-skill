#!/usr/bin/env python3
"""Validate and render a draw.io (.drawio) file.

Two jobs, both run by default:
  1. Validate the XML is well-formed and is a real draw.io document. Pure
     stdlib, so this always works even with nothing installed.
  2. Export to PNG/SVG/PDF using the draw.io desktop engine (real mxGraph
     fidelity). Skipped with a clear install hint if the binary is missing.

Usage:
  python3 render.py diagram.drawio                  # validate + export PNG next to it
  python3 render.py diagram.drawio --format svg     # export SVG instead
  python3 render.py diagram.drawio --validate-only  # just well-formedness + counts
  python3 render.py diagram.drawio -o out/pic.png   # explicit output path

Exit codes: 0 ok, 1 invalid XML / not a drawio file, 2 export failed,
3 export skipped because no renderer found (validation still passed).
"""
import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

# Candidate locations for the draw.io desktop binary, in priority order.
BINARY_CANDIDATES = [
    "/Applications/draw.io.app/Contents/MacOS/draw.io",
    "/Applications/drawio.app/Contents/MacOS/drawio",
    "/usr/bin/drawio",
    "/usr/local/bin/drawio",
    "/opt/drawio/drawio",
    r"C:\Program Files\draw.io\draw.io.exe",
    r"C:\Program Files (x86)\draw.io\draw.io.exe",
]

INSTALL_HINT = {
    "darwin": "brew install --cask drawio",
    "linux": "sudo snap install drawio   (or grab a .deb/.AppImage from "
             "https://github.com/jgraph/drawio-desktop/releases)",
    "win32": "winget install JGraph.Draw",
}


def install_hint():
    return INSTALL_HINT.get(sys.platform, "see https://github.com/jgraph/drawio-desktop/releases")


def find_binary():
    for name in ("drawio", "draw.io"):
        on_path = shutil.which(name)
        if on_path:
            return on_path
    for cand in BINARY_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None


def _pages(root):
    """[(page_label, [mxCell, ...]), ...].

    Coordinates only mean something WITHIN a page. Collecting every mxCell in the
    file and comparing them as one plane makes page 2's boxes look like they sit
    on top of page 1's, which is how a clean 3-page diagram once produced 225
    bogus overlap warnings.
    """
    diagrams = list(root.iter("diagram"))
    if not diagrams:
        return [(None, [c for c in root.iter("mxCell")])]
    return [
        (d.get("name") or f"page {i + 1}", [c for c in d.iter("mxCell")])
        for i, d in enumerate(diagrams)
    ]


def _compressed_pages(root):
    """Pages whose content is draw.io's compressed blob rather than plain XML."""
    out = []
    for i, d in enumerate(root.iter("diagram")):
        if len(d) == 0 and (d.text or "").strip():
            out.append(d.get("name") or f"page {i + 1}")
    return out


def validate(path):
    """Parse the file and confirm it is a draw.io document. Returns (nodes, edges)."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        print(f"INVALID: XML is not well-formed: {e}", file=sys.stderr)
        sys.exit(1)
    root = tree.getroot()
    # A .drawio file is <mxfile> at the root (possibly multi-page <diagram>),
    # or a bare <mxGraphModel>. Anything else is not a draw.io document.
    tags = {el.tag for el in root.iter()}
    if root.tag not in ("mxfile", "mxGraphModel") and "mxGraphModel" not in tags:
        print(
            f"INVALID: root <{root.tag}> is not a draw.io document "
            "(expected <mxfile> or <mxGraphModel>).",
            file=sys.stderr,
        )
        sys.exit(1)
    pages = _pages(root)
    cells = [c for _, page_cells in pages for c in page_cells]
    nodes = sum(1 for c in cells if c.get("vertex") == "1")
    edges = sum(1 for c in cells if c.get("edge") == "1")
    print(f"VALID: draw.io document, {len(pages)} page(s), {nodes} node(s), {edges} edge(s).")

    packed = _compressed_pages(root)
    if packed:
        print(
            f"NOTE: {len(packed)} page(s) store compressed content ({', '.join(packed[:3])}), "
            "so the cell-level checks cannot read them.\n"
            "  Turn off Extras > Compressed in the draw.io app and re-save to check them.",
            file=sys.stderr,
        )
    elif nodes == 0:
        print("WARNING: 0 vertices, the diagram is empty.", file=sys.stderr)

    multi = len(pages) > 1
    for label, page_cells in pages:
        check_geometry(page_cells, page=label if multi else None)
        check_text_wrap(page_cells, page=label if multi else None)
        check_text_height(page_cells, page=label if multi else None)
    return nodes, edges


def _label(cell, width=44):
    """First line of a cell's label, tags stripped, truncated for warning output.

    ElementTree has already decoded the XML entities, so a `&#10;` in the source
    arrives here as a real newline and `<br>` as literal text. Splitting on the
    raw entity (what this used to do) never matched, so multi-line labels came
    out as one run-on line.
    """
    raw = cell.get("value") or ""
    first = re.split(r"\n|<br\s*/?>", raw)[0]
    return re.sub(r"<[^>]+>", "", first).strip()[:width]


def _origin_resolver(cells):
    """Absolute (x, y) of any cell, following the parent chain.

    mxGraph geometry is relative to the PARENT cell, not the page. Hand-authored
    files usually put every vertex on the root layer so relative == absolute, but
    the draw.io UI reparents a box the moment you drop it inside a container. In
    those files the raw x/y of a child and of a top-level box live in different
    coordinate spaces, and comparing them directly is meaningless.
    """
    by_id = {c.get("id"): c for c in cells if c.get("id") is not None}
    memo = {}

    def origin(cid, seen=frozenset()):
        if cid in memo:
            return memo[cid]
        cell = by_id.get(cid)
        if cell is None or cid in seen:          # missing parent, or a cycle
            return (0.0, 0.0)
        px, py = origin(cell.get("parent"), seen | {cid})
        x = y = 0.0
        geo = cell.find("mxGeometry")
        # Only a vertex offsets its children; a layer or an edge contributes nothing.
        if cell.get("vertex") == "1" and geo is not None:
            try:
                x, y = float(geo.get("x") or 0), float(geo.get("y") or 0)
            except (TypeError, ValueError):
                x = y = 0.0
        memo[cid] = (px + x, py + y)
        return memo[cid]

    return origin


def _rects(cells, keep=None):
    """(id, label, x, y, w, h) for every vertex carrying a geometry, in ABSOLUTE coords.

    `keep` filters which vertices are RETURNED. Origins still resolve over the full
    cell list, so excluding a cell never corrupts a sibling's absolute position.
    """
    origin = _origin_resolver(cells)
    out = []
    for c in cells:
        if c.get("vertex") != "1":
            continue
        if keep is not None and not keep(c):
            continue
        g = c.find("mxGeometry")
        if g is None:
            continue
        try:
            w, h = float(g.get("width", 0)), float(g.get("height", 0))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        x, y = origin(c.get("id"))
        out.append((c.get("id"), _label(c), x, y, w, h))
    return out


def _where(page):
    return f" on '{page}'" if page else ""


def _is_overlay(cell):
    """An icon/logo deliberately drawn on top of another node.

    The grammar puts real brand marks in a diagram by centering a `shape=image`
    cell over its bubble, so every one of those is a partial overlap on purpose.
    Reporting them buries the accidental overlaps this check exists to find.
    """
    style = cell.get("style") or ""
    return "shape=image" in style or style.startswith("image;") or "image=data:" in style


def check_geometry(cells, tol=2.0, page=None):
    """Warn on PARTIAL overlaps between boxes.

    Containment is legitimate and everywhere: a stage container holds its boxes.
    A PARTIAL overlap is the bug shape, and it is the one nobody sees in the XML.
    A measured case: raising a stage container's height pushed it into the footer
    text, which rendered as two lines of prose printed on top of each other. XML
    validation called that file VALID and the draw.io export succeeded, because
    neither looks at coordinates. Only opening the PNG caught it, which is exactly
    the check a busy session skips.

    Advisory: prints and returns a count, never exits non-zero. A diagram can have a
    deliberate overlap, and a validator that blocks on style would stop being run.
    """
    rects = _rects(cells, keep=lambda c: not _is_overlay(c))
    hits = []
    for i in range(len(rects)):
        _, la, ax, ay, aw, ah = rects[i]
        for j in range(i + 1, len(rects)):
            _, lb, bx, by, bw, bh = rects[j]
            ox = min(ax + aw, bx + bw) - max(ax, bx)
            oy = min(ay + ah, by + bh) - max(ay, by)
            if ox <= tol or oy <= tol:
                continue                                   # disjoint or just touching
            a_in_b = bx - tol <= ax and by - tol <= ay and \
                ax + aw <= bx + bw + tol and ay + ah <= by + bh + tol
            b_in_a = ax - tol <= bx and ay - tol <= by and \
                bx + bw <= ax + aw + tol and by + bh <= ay + ah + tol
            if a_in_b or b_in_a:
                continue                                   # containment is fine
            hits.append((la or "(unlabelled)", lb or "(unlabelled)", ox, oy))
    if hits:
        print(f"WARNING: {len(hits)} partial box overlap(s){_where(page)}, which render "
              f"as text printed over text:", file=sys.stderr)
        for la, lb, ox, oy in hits[:10]:
            print(f"  '{la}'  x  '{lb}'  (overlap {ox:.0f}x{oy:.0f}px)", file=sys.stderr)
        print("  Containment is fine; PARTIAL overlap is the bug. Look at the PNG.",
              file=sys.stderr)
    return len(hits)


_BLOCK_SPLIT = re.compile(r"\n|<br\s*/?>|</?(?:div|p|li|tr|h[1-6])\b[^>]*>", re.I)


def _lines(cell):
    """Label split into rendered lines, HTML tags stripped.

    Splits on the block tags the draw.io UI emits, not only on <br>. A label typed
    in the app arrives as <div> or <p> blocks, so a <br>-only split reads a
    multi-line label as one very long line and both text checks misjudge it.
    """
    raw = cell.get("value") or ""
    parts = _BLOCK_SPLIT.split(raw)
    return [re.sub(r"<[^>]+>", "", p).replace("&nbsp;", " ").strip() for p in parts]


def check_text_wrap(cells, slack=1.15, char_em=0.5, page=None):
    """Warn on plain text cells that CANNOT wrap and whose label overruns the box.

    A `text;html=1` cell without `whiteSpace=wrap` does not wrap. Long prose runs
    straight past the cell's width, over whatever sits beside it, and nothing
    reports it: XML validation passes and the draw.io export succeeds, so the page
    is unreadable while every check is green. It lands on the plain text cells used
    for header body copy, footers, and notes, because shape cells are usually
    authored with wrap already on.

    Only the geometry is checkable, not the real font metrics, so this estimates
    the widest line at `char_em` * fontSize per character and allows `slack`
    before complaining. Short titles therefore stay quiet. Advisory like
    check_geometry: prints, returns a count, never exits non-zero.
    """
    hits = []
    for c in cells:
        if c.get("vertex") != "1":
            continue
        style = c.get("style") or ""
        # Any cell without whiteSpace=wrap renders each authored line as ONE line and
        # lets it run out the side. This used to be filtered to `text;` cells only, on
        # the assumption that shape cells are authored with wrap already on. They are
        # not: the boxes that carry body copy (legends, notes) are ordinary rectangles,
        # so the check skipped exactly the cells most likely to hold a long sentence.
        # Filter on the property that actually causes the bug, not on the shape.
        if "whiteSpace=wrap" in style or _is_overlay(c):
            continue
        geo = c.find("mxGeometry")
        if geo is None:
            continue
        try:
            w = float(geo.get("width") or 0)
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        m = re.search(r"fontSize=(\d+(?:\.\d+)?)", style)
        size = float(m.group(1)) if m else 12.0
        longest = max(_lines(c), key=len, default="")
        est = len(longest) * size * char_em
        if est > w * slack:
            hits.append((_label(c), est, w))
    if hits:
        print(f"WARNING: {len(hits)} text cell(s){_where(page)} without whiteSpace=wrap "
              f"whose label looks wider than the cell:", file=sys.stderr)
        for label, est, w in hits[:10]:
            print(f"  '{label or '(unlabelled)'}'  (~{est:.0f}px of text in a {w:.0f}px cell)",
                  file=sys.stderr)
        print("  Add whiteSpace=wrap to the style, or widen the cell. Look at the PNG.",
              file=sys.stderr)
    return len(hits)


def check_text_height(cells, slack=1.2, char_em=0.5, line_em=1.35, page=None):
    """Warn on any labelled box whose text needs more VERTICAL space than the box has.

    check_text_wrap catches a long line running out the SIDE of a plain `text;` cell.
    This catches the other direction, and it is the one that bites when you EDIT a
    diagram: you add a sentence to an existing note and the box keeps its authored
    height, so the extra lines render straight through the bottom border and over
    whatever sits below. Nothing reports it. The XML is valid, the export succeeds,
    and the geometry checker is happy because the BOXES do not overlap, only the
    spilled text does. That is exactly how a broken diagram passes every check.

    It also covers shape cells, not just `text;` ones, because the boxes that carry
    body copy (legends, trust statements, footers) are usually ordinary rectangles
    and were skipped entirely by the width check.

    Real font metrics are unavailable, so this estimates: each authored line wraps
    into ceil(chars * fontSize * char_em / usable width) visual lines, and each
    visual line costs fontSize * line_em. `slack` keeps it quiet on near misses.
    Advisory like the others: prints, returns a count, never exits non-zero.
    """
    hits = []
    for c in cells:
        if c.get("vertex") != "1" or _is_overlay(c):
            continue
        lines = [ln for ln in _lines(c) if ln]
        if not lines:
            continue
        geo = c.find("mxGeometry")
        if geo is None:
            continue
        try:
            w = float(geo.get("width") or 0)
            h = float(geo.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        style = c.get("style") or ""
        m = re.search(r"fontSize=(\d+(?:\.\d+)?)", style)
        size = float(m.group(1)) if m else 12.0
        pad = 0.0
        for key in ("spacingLeft", "spacingRight", "spacingTop"):
            sm = re.search(rf"{key}=(\d+(?:\.\d+)?)", style)
            if sm:
                pad += float(sm.group(1))
        usable = max(w - pad - 8.0, 20.0)
        visual = 0
        for ln in lines:
            est = len(ln) * size * char_em
            visual += max(1, math.ceil(est / usable))
        needed = visual * size * line_em
        if needed > h * slack:
            hits.append((_label(c), needed, h, visual))
    if hits:
        print(f"WARNING: {len(hits)} box(es){_where(page)} whose text needs more height "
              f"than the box has, so the text renders past the border:", file=sys.stderr)
        for label, needed, h, visual in hits[:10]:
            print(f"  '{label or '(unlabelled)'}'  (~{visual} lines, ~{needed:.0f}px of text "
                  f"in a {h:.0f}px box)", file=sys.stderr)
        print("  Grow the box height, widen it, or cut the text. Look at the PNG.",
              file=sys.stderr)
    return len(hits)

def export(binary, src, out, fmt, scale, border):
    cmd = [
        binary, "--export",
        "--format", fmt,
        "--output", out,
        "--scale", str(scale),
        "--border", str(border),
    ]
    # Electron apps need these to run without a sandboxed renderer / GPU.
    cmd += ["--no-sandbox", "--disable-gpu"]
    cmd.append(src)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(out):
        print("EXPORT FAILED:", file=sys.stderr)
        print(res.stdout, file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        sys.exit(2)
    print(f"EXPORTED: {out}")


def main():
    ap = argparse.ArgumentParser(description="Validate and render a .drawio file.")
    ap.add_argument("file", help="path to the .drawio source")
    ap.add_argument("-f", "--format", default="png", choices=["png", "svg", "pdf"])
    ap.add_argument("-o", "--output", help="output path (default: alongside source)")
    ap.add_argument("--scale", default=2, type=float, help="render scale (default 2 = retina)")
    ap.add_argument("--border", default=10, type=int, help="border px (default 10)")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"No such file: {args.file}", file=sys.stderr)
        sys.exit(1)

    validate(args.file)
    if args.validate_only:
        return

    binary = find_binary()
    if not binary:
        print(
            "EXPORT SKIPPED: no draw.io renderer found.\n"
            f"  Install it with:  {install_hint()}\n"
            "  (The .drawio file is valid and editable as-is.)",
            file=sys.stderr,
        )
        sys.exit(3)

    out = args.output or os.path.splitext(args.file)[0] + "." + args.format
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    export(binary, args.file, out, args.format, args.scale, args.border)


if __name__ == "__main__":
    main()
