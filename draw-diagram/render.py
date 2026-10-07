#!/usr/bin/env python3
"""Validate and render a draw.io (.drawio) file.

Two jobs, both run by default:
  1. Validate the XML is well-formed and is a real draw.io document, then run
     the advisory checks per page: partial box overlaps, text that cannot wrap,
     text taller than its box (a cylinder or document has less room), edge ends
     that name no cell, edges of different colours at one exit point, boxes
     hidden under a filled box painted after them, Hebrew lines with a Latin run
     at an edge and no direction set, and label text with an em dash, a curly
     quote or a box-drawing character.
     Pure stdlib, so this always works even with nothing installed.
     The checks print WARNING lines on stderr and never change the exit code.
  2. Export to PNG/SVG/PDF using the draw.io desktop engine (real mxGraph
     fidelity). Every page is exported: PNG and SVG as <name>-p1, <name>-p2, ...
     when there are several pages, a PDF as one file. Skipped with a clear
     install hint if the binary is missing.

PNG size: without --scale the scale is 2, lowered per page to keep the PNG
under RASTER_FIT_MP (a NOTE on stderr). draw.io cuts off the bottom of a PNG
past about RASTER_LIMIT_MP while still reporting success, so past that size the
PNG is read (with Pillow) and a cut-off is an export failure.

Crops: a PNG page wider or taller than CROP_OVER_PX is also cut, at full size,
into one crop per top-level stage container plus a header and a footer band,
then a plain rest-N crop over any ink none of those holds (Pillow, else macOS
sips, else a NOTE with the pixel boxes), because the Read tool shrinks a wide
page until its labels cannot be read. The crops go in <page>.crops/, a folder
render.py makes with a marker file and empties of its own crops on each run.
Pictures of an older page count are never deleted automatically (a NOTE names
them); only this export's own page picture is replaced, or removed when the new
one is cut off. --no-crops skips them.
Every export ends with a LOOK FOR line naming what no check can see.

Usage:
  python3 render.py diagram.drawio                  # validate + export PNG next to it
  python3 render.py diagram.drawio --format svg     # export SVG instead
  python3 render.py diagram.drawio --validate-only  # every check, no export
  python3 render.py diagram.drawio -o out/pic.png   # explicit output path
  python3 render.py diagram.drawio --scale 1        # explicit scale, always used

Output lines (stdout), one per exported file:
  EXPORTED: <path>                          (single page)
  EXPORTED: <path> [page N of M: <label>]   (each page of a multi-page file)
  EXPORTED: <path> [all M pages]            (a multi-page PDF)
  CROP: <path><tab>[<label>]                (each crop, after its page's line)
  LOOK FOR (no check can see these): ...    (once, at the end of an export)

Exit codes: 0 ok, 1 invalid XML / not a drawio file, 2 export failed (including
a cut-off PNG), 3 export skipped because no renderer found (validation still
passed).
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
        check_edge_ends(page_cells, page=label if multi else None)
        check_edge_fan(page_cells, page=label if multi else None)
        check_paint_order(page_cells, page=label if multi else None)
        check_hebrew_direction(page_cells, page=label if multi else None)
        check_label_prose(page_cells, page=label if multi else None)
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
    Overlapping boxes render as prose printed over prose while the XML is
    valid and the export succeeds, so this check reads the coordinates.

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


# draw.io breaks a label on <br>, but the UI also emits block tags: type a multi-line
# note in the app and the lines come back wrapped in <div>, and pasted content brings
# <p> and <li>. Splitting on <br> alone joins all of those into one huge pseudo-line,
# so the width check would over-report. Split on the block tags too.
_BLOCK_SPLIT = re.compile(r"\n|<br\s*/?>|</?(?:div|p|li|tr|h[1-6])\b[^>]*>", re.I)


def _lines(cell):
    """Label split into rendered lines, HTML tags stripped."""
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
        # not: the boxes that carry body copy (legends, trust statements, notes) are
        # ordinary rectangles, so the check skipped exactly the cells most likely to
        # hold a long sentence, and a statement box overflowing its border rendered
        # green. Filter on the property that actually causes the bug, not on the shape.
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
        # E5: a cylinder's caps and a document's wavy foot take height the text
        # cannot use (measured on draw.io renders, DRAWIO_DIAGRAMS.md "Cylinders and
        # documents need more height than a rectangle").
        shape = _shape_name(*_style(c))
        room = h - SHAPE_LOST_HEIGHT.get(shape, 0.0)
        if needed > max(room, 0.0) * slack:
            hits.append((_label(c), needed, h, visual,
                         shape if shape in SHAPE_LOST_HEIGHT else None, room))
    if hits:
        print(f"WARNING: {len(hits)} box(es){_where(page)} whose text needs more height "
              f"than the box has, so the text renders past the border:", file=sys.stderr)
        for label, needed, h, visual, shape, room in hits[:10]:
            inside = (f" (a {shape}: about {room:.0f}px of it holds text)"
                      if shape else "")
            print(f"  '{label or '(unlabelled)'}'  (~{visual} lines, ~{needed:.0f}px of text "
                  f"in a {h:.0f}px box{inside})", file=sys.stderr)
        print("  Grow the box height, widen it, or cut the text. Look at the PNG.",
              file=sys.stderr)
    return len(hits)


def _style(cell):
    """A cell's style as (first-word flags, {key: value})."""
    flags, kv = set(), {}
    for part in (cell.get("style") or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k.strip()] = v.strip()
        else:
            flags.add(part)
    return flags, kv


def _coord(value):
    """A style coordinate compared by value: 1 and 1.0 are one exit point.
    An unparseable value is returned as its stripped string."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return (value or "").strip()


def _end_point(edge, which):
    geo = edge.find("mxGeometry")
    if geo is None:
        return None
    return geo.find(f"mxPoint[@as='{which}Point']")


def check_edge_ends(cells, page=None):
    """Warn on an edge end that has no source/target id AND no point, or an id that
    is not a cell on this page.

    Such an edge draws nothing, while the validator still counts it as an edge.
    An end with a sourcePoint/targetPoint is fine: that is how a free-standing
    legend arrow is written.
    """
    ids = {c.get("id") for c in cells if c.get("id") is not None}
    hits = []
    for e in cells:
        if e.get("edge") != "1":
            continue
        problems = []
        for which in ("source", "target"):
            ref = e.get(which)
            if ref:
                if ref not in ids:
                    problems.append(f"{which} '{ref}' is not a cell on this page")
            elif _end_point(e, which) is None:
                problems.append(f"no {which} and no {which}Point")
        if problems:
            hits.append((e.get("id"), _label(e), problems))
    if hits:
        print(f"WARNING: {len(hits)} edge(s){_where(page)} with an end that names no cell "
              "and has no point, so draw.io draws nothing for them:", file=sys.stderr)
        for eid, label, problems in hits[:10]:
            name = f"'{eid}'" + (f" ({label})" if label else "")
            print(f"  edge {name}: {'; '.join(problems)}", file=sys.stderr)
        print("  Set source and target to cell ids on the same page, or give the end a "
              "point. Look at the PNG.", file=sys.stderr)
    return len(hits)


def check_edge_fan(cells, page=None):
    """Warn when edges leave one box at the same explicit exit point in different
    colours.

    They share one trunk, and the edge drawn last paints over the others, so the
    picture shows one colour where the diagram means several. A same-colour fan is
    a bus that splits and stays quiet.
    """
    labels = {c.get("id"): _label(c) for c in cells}
    groups = {}
    for e in cells:
        if e.get("edge") != "1" or not e.get("source"):
            continue
        _, kv = _style(e)
        if "exitX" not in kv or "exitY" not in kv:
            continue
        ex, ey = _coord(kv["exitX"]), _coord(kv["exitY"])
        # A missing strokeColor is draw.io's default edge colour, black on a light
        # page, so it equals an explicit #000000; compare colours by value, not
        # spelling, so #9673a6 and #9673A6 are one colour. A colour name (red)
        # keeps its own name rather than collapsing into "unreadable".
        raw = kv.get("strokeColor")
        colour = _rgb(raw, "#000000")
        if colour is None:
            colour = raw.strip().lower()
        key = (e.get("source"), ex, ey)
        groups.setdefault(key, []).append((e.get("id"), kv.get("strokeColor", "default"), colour))
    hits = []
    for (src, ex, ey), edges in groups.items():
        if len(edges) > 1 and len({col for _, _, col in edges}) > 1:
            hits.append((src, ex, ey, [(eid, shown) for eid, shown, _ in edges]))
    if hits:
        print(f"WARNING: {len(hits)} box(es){_where(page)} with edges in different colours "
              "leaving at the same exit point; the last one drawn covers the others:",
              file=sys.stderr)
        for src, ex, ey, edges in hits[:10]:
            name = labels.get(src) or src
            listed = ", ".join(f"{eid} ({col})" for eid, col in edges)
            print(f"  from '{name}' at exitX={ex},exitY={ey}: {listed}", file=sys.stderr)
        print("  Give each edge its own exitX/exitY. Look at the PNG.", file=sys.stderr)
    return len(hits)


def _paints_fill(cell):
    """Whether a vertex paints a solid fill that could hide what is under it.

    Follows draw.io's own fill rules. Unfilled shapes do not hide anything, so
    they are never the cause of a hidden box:
      - an image overlay, or a text / group / edgeLabel with no fill;
      - fillColor=none;
      - a line shape (shape=line / a connector), which only strokes, never fills;
      - opacity or fillOpacity 0 (any numeric spelling), or a fill translucent
        enough to see through (effective alpha below 0.9);
      - a swimlane whose body (swimlaneFillColor) is unset or none, even when its
        title bar is filled.
    """
    if _is_overlay(cell):
        return False
    flags, kv = _style(cell)
    shape = _shape_name(flags, kv)
    if shape == "line" or "line" in flags:
        return False
    opacity = _num(kv, "opacity", 100)
    fill_opacity = _num(kv, "fillOpacity", 100)
    if opacity is not None and fill_opacity is not None and \
            (opacity / 100.0) * (fill_opacity / 100.0) < 0.9:
        return False
    # A swimlane's body is painted by swimlaneFillColor alone, whatever its title
    # bar's fillColor says (fillColor=none with a filled body still hides).
    if shape == "swimlane":
        body = kv.get("swimlaneFillColor")
        return body is not None and body.strip().lower() != "none"
    fill = kv.get("fillColor")
    if fill is not None and fill.strip().lower() == "none":
        return False
    if flags & {"text", "group", "edgeLabel"} and fill is None:
        return False
    return True


def _covers(cover, rect, inner):
    """Whether a filled shape `cover` (its _style flags+kv) with bounding box
    `rect` (x, y, w, h) paints over the whole of `inner` (x, y, w, h).

    A rectangle or swimlane fills its whole box. An ellipse or a rhombus
    (diamond) only paints part of it, so an inner box inside the box but outside
    the painted area is not hidden: all four of its corners must lie in the
    painted region. Any other shape is not reasoned about here, so it does not
    raise a hidden-box warning on bounding box alone."""
    flags, kv = cover
    shape = _shape_name(flags, kv)
    if shape in ("rect", "swimlane"):
        return True
    ax, ay, aw, ah = rect
    cx, cy, rx, ry = ax + aw / 2, ay + ah / 2, aw / 2, ah / 2
    if rx <= 0 or ry <= 0:
        return False
    bx, by, bw, bh = inner
    corners = [(bx, by), (bx + bw, by), (bx, by + bh), (bx + bw, by + bh)]
    if shape in ("ellipse", "circle"):
        return all(((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1 for px, py in corners)
    if shape in ("rhombus", "mxgraph.basic.diamond"):
        return all(abs(px - cx) / rx + abs(py - cy) / ry <= 1 for px, py in corners)
    return False


def _paint_order(cells):
    """{id: index} in the order draw.io paints: the parent tree, depth first,
    children in document order. A child is always painted after its parent, so a
    container that parents its boxes cannot hide them wherever it sits in the XML."""
    kids, ids = {}, set()
    for c in cells:
        cid = c.get("id")
        if cid is not None:
            ids.add(cid)
    roots = []
    for c in cells:
        parent = c.get("parent")
        if parent is None or parent not in ids or parent == c.get("id"):
            roots.append(c)
        else:
            kids.setdefault(parent, []).append(c)
    order, seen = {}, set()
    stack = list(reversed(roots))
    while stack:
        c = stack.pop()
        cid = c.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        order[cid] = len(order)
        stack.extend(reversed(kids.get(cid, [])))
    return order


def check_paint_order(cells, tol=0.5, page=None):
    """Warn on a box hidden under a FILLED box that is painted after it.

    draw.io paints in document order (children after their parent), so a band or
    stage container written after the boxes it holds covers them completely. The
    document validates, the node count is right, and the overlap check reads the
    containment as fine. Only a filled later box can hide anything: an unfilled
    ring, a container that parents its boxes, and an image overlay stay quiet.
    """
    by_id = {c.get("id"): c for c in cells if c.get("id") is not None}

    def on_edge(c):
        p = by_id.get(c.get("parent"))
        return p is not None and p.get("edge") == "1"

    order = _paint_order(cells)
    rects = _rects(cells, keep=lambda c: not on_edge(c))

    # An ancestor is always painted before its descendants in tree order, so the
    # order test below already keeps a container from "hiding" its own boxes.
    hits = []
    for bid, blabel, bx, by, bw, bh in rects:
        for aid, alabel, ax, ay, aw, ah in rects:
            if aid == bid or aw * ah <= bw * bh:
                continue
            inside = (ax - tol <= bx and ay - tol <= by and
                      bx + bw <= ax + aw + tol and by + bh <= ay + ah + tol)
            if not inside or order.get(aid, -1) <= order.get(bid, -1):
                continue
            if not _paints_fill(by_id[aid]):
                continue
            if not _covers(_style(by_id[aid]), (ax, ay, aw, ah), (bx, by, bw, bh)):
                continue
            hits.append((blabel or bid, alabel or aid))
            break
    if hits:
        print(f"WARNING: {len(hits)} box(es){_where(page)} hidden under a filled box "
              "written after them in the XML:", file=sys.stderr)
        for blabel, alabel in hits[:10]:
            print(f"  '{blabel}'  under  '{alabel}'", file=sys.stderr)
        print("  Write the container before its contents, or make it their parent. "
              "Look at the PNG.", file=sys.stderr)
    return len(hits)


# ---------------------------------------------------------------------------
# Raster size. Measured on draw.io 31.4.5 (macOS) with a synthetic page
# at scale 2, border 10: every PNG at or under 32.2 MP came out complete; every PNG
# from 32.5 MP up was cut off, except one 2035 px wide page at 32.6 MP. The limit is
# in OUTPUT pixels (a 36.3 MP export at scale 1 was cut too), and the export still
# exits 0 with a full-size file whose bottom is blank.
# ---------------------------------------------------------------------------
DEFAULT_SCALE = 2.0
# Auto-scale target, below the smallest cut measured (32.5 MP) with room for labels
# and edges that reach past the boxes the size is predicted from.
RASTER_FIT_MP = 30.0
# Past this size the PNG is read: one unbroken run of columns whose ink stops short
# of the boxes drawn there is a cut (ink_check).
RASTER_LIMIT_MP = 32.4
# Measured: just past the limit the ink stops at one row only in the
# columns right of a tile edge (x = 2048) and runs on in the left columns, so the
# whole-image ink box can still reach the bottom. Pixels are read one strip of
# INK_STRIP_PX columns at a time, and judged in groups of INK_GROUP_PX columns.
INK_STRIP_PX = 256
INK_GROUP_PX = 8
# A column group is short when its ink ends more than this many rows (plus one
# per unit of scale) above the bottom edge of the boxes drawn there. Measured:
# a complete export draws a box's bottom edge 1 to 2 rows BELOW the
# position computed from its geometry; the smallest cut seen (3000x2675 at scale
# 2) stops 14 rows above it.
INK_SHORT_ROWS = 6
# A cut takes whole tiles of columns, so a few short groups alone are not a cut.
INK_MIN_CUT_PX = 64
# Measured: near the cut a few pixels sit 1 level off the background
# colour, e.g. (245, 244, 244) on a (245, 245, 245) page. The blank band itself is
# the page background colour (opaque white on a page with no background).
INK_TOLERANCE = 16


_HEBREW = re.compile("[\u0590-\u05ff]")
_LATIN_RUN = r"[A-Za-z0-9._/\\-]{3,}"
_LATIN_AT_START = re.compile(r"^[\s\"'(\[]*(" + _LATIN_RUN + ")")
_LATIN_AT_END = re.compile("(" + _LATIN_RUN + r")[\s\"')\].,:;!?]*$")
# A real `dir` attribute with an exact value: not `data-dir`, not `dir="rtl-x"`.
_DIRECTION_SET = re.compile(r"""(?<![\w-])dir\s*=\s*(["']?)(?:rtl|ltr)\1(?![\w-])""", re.I)


def _visible_lines(cell):
    """A label's lines as the reader sees them: entities decoded (twice, for a
    generator that escaped once too often, and so `&#1491;` counts as Hebrew),
    split on line breaks and block tags, tags stripped, spaces squeezed."""
    import html
    text = html.unescape(html.unescape(cell.get("value") or ""))
    return [" ".join(re.sub(r"<[^>]+>", " ", p).split()) for p in _BLOCK_SPLIT.split(text)]


def check_hebrew_direction(cells, page=None):
    """E6: warn on a Hebrew label line that starts or ends with a Latin run (a file
    name, an extension, a code word) while the label sets no direction.

    A label's base direction is left-to-right unless it says otherwise, so the
    bidi order puts a trailing `.xlsx` at the visual start of a Hebrew line, and a
    Hebrew reader takes it for the first word. It lands on the lines that carry
    the facts (every file name). A label that sets dir="rtl" (the rule book's rtl()
    wrapper) or dir="ltr" (its path() wrapper for a mixed path) chose a direction
    and is quiet; so is an all-Hebrew line, which alignment alone renders right.
    Advisory: prints, returns a count, never exits non-zero.
    """
    import html
    hits = []
    for c in cells:
        decoded = html.unescape(html.unescape(c.get("value") or ""))
        if not _HEBREW.search(decoded) or _DIRECTION_SET.search(decoded):
            continue
        for ln in _visible_lines(c):
            if not _HEBREW.search(ln):
                continue
            ends = [side for side, edge in (("starts", _LATIN_AT_START.search(ln)),
                                            ("ends", _LATIN_AT_END.search(ln)))
                    if edge and re.search(r"[A-Za-z]", edge.group(1))]
            if ends:
                hits.append((c.get("id") or "?", ends[0]))
                break
    if hits:
        # The label text is not printed: it can hold a client's name, and the id
        # is enough to find the cell.
        print(f"WARNING: {len(hits)} Hebrew label(s){_where(page)} start or end with a "
              "Latin run and set no direction, so the run renders at the wrong end:",
              file=sys.stderr)
        for cid, side in hits[:10]:
            print(f"  cell {cid}: a Hebrew line {side} with a Latin run", file=sys.stderr)
        print('  Wrap a sentence in <div dir="rtl">, a file path in <span dir="ltr">'
              " (the rtl() and path() wrappers in the rule book). Look at the PNG.",
              file=sys.stderr)
    return len(hits)


# Characters the style check bans in any text: the em dash, curly quotes, and
# box-drawing characters.
_BANNED_CHARS = (("em dash", re.compile("\u2014")),
                 ("curly quote", re.compile("[\u2018\u2019\u201c\u201d]")),
                 ("box-drawing character", re.compile("[\u2500-\u257f]")))
PROSE_WORDS = ()


def _label_text(cell):
    """A label as the reader sees it: tags stripped, entities decoded twice (a
    generator that escaped once too often still reads right)."""
    import html
    raw = cell.get("value") or ""
    text = html.unescape(html.unescape(raw))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", " ", text)


def check_label_prose(cells, page=None):
    """E7: warn on labels carrying a character the style check bans (em dash,
    curly quote, box-drawing), since a script-generated diagram never goes
    through an editor's or CI's prose checks. Advisory: prints, returns a count.

    A page whose labels hold text the scan cannot read is a broken scan, not a
    clean page, so it says so (the control the rule book asks for).
    """
    raw_any = any((c.get("value") or "").strip() for c in cells)
    texts = [(c.get("id") or "?", _label_text(c)) for c in cells
             if (c.get("value") or "").strip()]
    if raw_any and not any(t.strip() for _cid, t in texts):
        print(f"WARNING: the label scan{_where(page)} read no text from labels that are "
              "not empty; their prose was not checked.", file=sys.stderr)
        return 1
    hits = []
    for cid, text in texts:
        found = [name for name, rx in _BANNED_CHARS if rx.search(text)]
        # Spaces squeezed, so a phrase split by a tag or a line break still reads
        # as the phrase the reader sees.
        low = " ".join(text.split()).lower()
        found += [name for rx, name in PROSE_WORDS if re.search(rx, low)]
        if found:
            hits.append((cid, found))
    if hits:
        # The label text is not printed: it would print the very character it
        # warns about, and a label can hold a client's name.
        print(f"WARNING: {len(hits)} label(s){_where(page)} carry text the style check "
              "bans:", file=sys.stderr)
        for cid, found in hits[:10]:
            print(f"  cell {cid}: {', '.join(found)}", file=sys.stderr)
        print("  Use a comma, a colon or parentheses for a dash, straight quotes, and "
              "plain words.", file=sys.stderr)
    return len(hits)


def page_extent(cells):
    """(width, height) of the boxes on one page, in diagram units, or None."""
    rects = _rects(cells)
    if not rects:
        return None
    x0 = min(r[2] for r in rects)
    y0 = min(r[3] for r in rects)
    x1 = max(r[2] + r[4] for r in rects)
    y1 = max(r[3] + r[5] for r in rects)
    return x1 - x0, y1 - y0


def predicted_mp(w, h, scale, border):
    """Predicted PNG size in megapixels.
    Measured: a 1000x1000 page at
    scale 2, border 10 exports at 2035x2035, so the size is extent * scale plus
    about border + 25 px; 2 * border + 20 covers it with a little to spare."""
    pad = 2 * border + 20
    return (w * scale + pad) * (h * scale + pad) / 1e6


def _fmt_scale(s):
    return f"{s:g}"


def choose_scale(cells, requested, border, page=None):
    """(scale, note or None) for one page's PNG export.

    `requested` None means pick: scale 2, lowered in steps of 0.05 until the
    predicted raster fits RASTER_FIT_MP. An explicit --scale is always obeyed;
    past the measured limit the note says so and the ink check verifies the result.
    """
    ext = page_extent(cells)
    if requested is not None:
        if ext:
            mp = predicted_mp(ext[0], ext[1], requested, border)
            if mp > RASTER_LIMIT_MP:
                return requested, (
                    f"NOTE: --scale {_fmt_scale(requested)} kept as asked{_where(page)}: the "
                    f"page is about {mp:.0f} MP at that scale, past the measured limit of "
                    f"about {RASTER_LIMIT_MP:.0f} MP, so the PNG is checked for a cut-off. "
                    "Leave --scale out to have it chosen.")
        return requested, None
    if not ext:
        return DEFAULT_SCALE, None
    w, h = ext
    if predicted_mp(w, h, DEFAULT_SCALE, border) <= RASTER_FIT_MP:
        return DEFAULT_SCALE, None
    scale = math.floor(math.sqrt(RASTER_FIT_MP * 1e6 / (w * h)) * 20) / 20
    while scale > 0.05 and predicted_mp(w, h, scale, border) > RASTER_FIT_MP:
        scale = round(scale - 0.05, 2)
    scale = max(scale, 0.05)
    return scale, (
        f"NOTE: scale lowered from {_fmt_scale(DEFAULT_SCALE)} to {_fmt_scale(scale)}"
        f"{_where(page)}: the page is {w:.0f}x{h:.0f}, about "
        f"{predicted_mp(w, h, DEFAULT_SCALE, border):.0f} MP at scale 2, and draw.io cuts "
        f"off the bottom of a PNG past about {RASTER_LIMIT_MP:.0f} MP. To choose the scale "
        f"yourself, pass --scale <value>, for example --scale {_fmt_scale(scale)}; a value "
        "that goes past the limit is still used, and a cut-off PNG fails the export.")


def _load_pil():
    """(Image, ImageChops) or None when Pillow is not installed."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    return Image, ImageChops


def _rgb(value, default):
    """A style colour as (r, g, b), 'none', or None when it cannot be read."""
    if value is None or value.strip().lower() == "default":
        value = default
    if value is None:
        return None
    v = value.strip().lower()
    if v == "none":
        return "none"
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _num(kv, key, default):
    try:
        return float(kv.get(key, default))
    except (TypeError, ValueError):
        return None


# Named styles that draw.io paints as a plain rectangle.
_RECT_SHAPES = {"rect", "rectangle", "process", "swimlane", "label"}
# Height a shape spends on its own outline, so its text cannot use it: a
# cylinder's top cap and bottom curve, a document's wavy foot. Measured on
# draw.io renders (DRAWIO_DIAGRAMS.md, the cylinder section).
SHAPE_LOST_HEIGHT = {"cylinder3": 46.0, "document": 28.0}


def _shape_name(flags, kv):
    """The shape a vertex is painted as: 'rect' for a plain box, else its name.

    A first-word style name ("ellipse;", "text;") picks a named style, and
    shape=... overrides it. An unknown name is returned as is, so callers that
    only trust rectangles skip it.
    """
    if "shape" in kv:
        names = [kv["shape"]]
    else:
        names = sorted(flags)
    other = [n for n in names if n not in _RECT_SHAPES]
    if other:
        return other[0]
    return "swimlane" if "swimlane" in names else "rect"


def _hidden(cell, by_id, collapsed=True):
    """True when the cell or an ancestor is invisible, or an ancestor is collapsed.

    draw.io leaves those out of the exported picture and out of its bounds. With
    collapsed=False only invisibility counts: an edge joined to a cell inside a
    collapsed group is still drawn, to the group (getVisibleTerminal)."""
    if cell.get("visible") == "0":
        return True
    seen = set()
    pid = cell.get("parent")
    while pid in by_id and pid not in seen:
        seen.add(pid)
        p = by_id[pid]
        if p.get("visible") == "0" or (collapsed and p.get("collapsed") == "1"):
            return True
        pid = p.get("parent")
    return False


def _shown_rects(cells):
    """[(cell, x, y, w, h)] in absolute units for every vertex the export draws
    from its own geometry: not hidden, not a label riding on an edge, not a
    relative geometry."""
    by_id = {c.get("id"): c for c in cells if c.get("id") is not None}

    def keep(c):
        p = by_id.get(c.get("parent"))
        if p is not None and p.get("edge") == "1":
            return False
        g = c.find("mxGeometry")
        if g is not None and g.get("relative") == "1":
            return False
        return not _hidden(c, by_id)

    return [(by_id[r[0]] if r[0] in by_id else None,) + r[2:]
            for r in _rects(cells, keep=keep)]


def _inks_bottom(cell, bg):
    """Whether a vertex paints a solid line of ink along its bottom edge, on a
    page whose background is `bg`. Only plain unrotated rectangles qualify; an
    ellipse, a rounded corner or a dashed line does not reach the bottom in every
    column, and text sits wherever its alignment puts it."""
    flags, kv = _style(cell)
    if _shape_name(flags, kv) not in ("rect", "swimlane"):
        return False
    if (_num(kv, "rotation", 0) or 0) % 360:
        return False
    alpha = (_num(kv, "opacity", 100) or 0) / 100.0

    def shows(colour, extra_alpha):
        if colour in (None, "none") or extra_alpha is None:
            return False
        diff = max(abs(a - b) for a, b in zip(colour, bg))
        return diff * alpha * extra_alpha / 100.0 > 2 * INK_TOLERANCE

    stroke = _rgb(kv.get("strokeColor"), "#000000")
    width = _num(kv, "strokeWidth", 1)
    # draw.io's own defaults: a vertex with no strokeColor is black, no
    # strokeWidth is 1, no dashed key is a solid line. Each default is what the
    # export paints, so it is the right answer here, not a pass by omission.
    if (width or 0) > 0 and kv.get("dashed", "0") in ("0", "false") and \
            shows(stroke, _num(kv, "strokeOpacity", 100)):
        return True
    fill_key = "swimlaneFillColor" if "swimlane" in (flags | {kv.get("shape")}) else "fillColor"
    fill = _rgb(kv.get(fill_key), "#ffffff" if fill_key == "fillColor" else "none")
    grad = kv.get("gradientColor")
    if grad and grad.strip().lower() != "none":
        g = _rgb(grad, None)
        if not shows(g, _num(kv, "fillOpacity", 100)):
            return False
    return shows(fill, _num(kv, "fillOpacity", 100))


def ink_expectations(cells, W, border, scale, bg):
    """{column group index: lowest pixel row the boxes there must reach}.

    Measured on draw.io 31.4.5: the top-left of the drawn content
    lands at border * scale pixels, and a box's bottom edge lands 1 to 2 rows
    below border * scale + (bottom - top of content) * scale. Anything drawn
    above or left of the boxes (an edge, a label) moves the boxes down or right
    in the picture. Down only makes the expectation easier to meet; for right,
    the shift is at most the picture's extra width, and every box's columns are
    trimmed by that much on the left so a shifted box is still judged on its own
    columns.
    """
    shown = _shown_rects(cells)
    if not shown:
        return {}
    xmin = min(r[1] for r in shown)
    ymin = min(r[2] for r in shown)
    xmax = max(r[1] + r[3] for r in shown)
    origin = border * scale
    drift = max(0.0, W - (origin + (xmax - xmin) * scale + border + 8))
    want = {}
    for cell, x, y, w, h in shown:
        if cell is None or not _inks_bottom(cell, bg):
            continue
        _, kv = _style(cell)
        margin = 2 * scale + (_num(kv, "strokeWidth", 1) or 1) * scale
        if kv.get("rounded") in ("1", "true"):
            # A rounded corner rises off the bottom edge. mxGraph's rectangle
            # takes a radius of arcSize percent (default 15) of the shorter side,
            # or arcSize / 2 units (default 20) with absoluteArcSize; trim it.
            if kv.get("absoluteArcSize") in ("1", "true"):
                margin += (_num(kv, "arcSize", 20) or 20) / 2 * scale
            else:
                margin += min(w, h) * (_num(kv, "arcSize", 15) or 15) / 100 * scale
        left = origin + (x - xmin) * scale + margin + drift
        right = origin + (x + w - xmin) * scale - margin
        bottom = origin + (y + h - ymin) * scale
        g0 = int(math.ceil(left / INK_GROUP_PX))
        g1 = int(math.floor(right / INK_GROUP_PX))   # groups g0 .. g1 - 1 fit
        for g in range(max(g0, 0), min(g1, W // INK_GROUP_PX)):
            if bottom > want.get(g, -1):
                want[g] = bottom
    return want


def page_background(diagram):
    """The page colour a <diagram> (or a bare <mxGraphModel>) asks for, as the
    style string, or None when it sets none. Measured: the PNG's blank
    area is that colour, and opaque white when the page sets none."""
    model = diagram if diagram.tag == "mxGraphModel" else diagram.find("mxGraphModel")
    value = model.get("background") if model is not None else None
    if value is None or value.strip().lower() in ("", "none"):
        return None
    return value


def _background(im, background):
    """The page colour as (r, g, b). From the page's own setting when the caller
    has it (`background` None from a page means white); when the caller passes
    the string 'corners' or the setting cannot be read, the colour most of the
    four corners share. Never one pixel alone: with --border 0 a corner can be a
    drawn box."""
    if background != "corners":
        got = _rgb(background, "#ffffff")
        if got not in (None, "none"):
            return got
    W, H = im.size
    corners = [im.crop((x, y, x + 1, y + 1)).convert("RGB").getpixel((0, 0))
               for x, y in ((0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1))]
    best = max(corners, key=corners.count)
    return best if corners.count(best) > 1 else (255, 255, 255)


def _inflate(diagram):
    """The <mxGraphModel> inside a compressed <diagram>, or None.

    draw.io's compressed form is base64 of raw deflate of the URI-encoded XML."""
    import base64
    import urllib.parse
    import zlib
    try:
        raw = zlib.decompress(base64.b64decode((diagram.text or "").strip()), -15)
        model = ET.fromstring(urllib.parse.unquote(raw.decode("utf-8")))
    except (ValueError, zlib.error, ET.ParseError, UnicodeDecodeError):
        return None
    return model if model.tag == "mxGraphModel" else model.find(".//mxGraphModel")


def export_pages(root):
    """[(label, cells, background)] for export, in page order.

    Unlike _pages(), a compressed page is inflated here, so its size can be
    predicted and its PNG checked; the validation checks still skip it."""
    diagrams = list(root.iter("diagram"))
    if not diagrams:
        model = root if root.tag == "mxGraphModel" else root.find(".//mxGraphModel")
        return [(None, list(root.iter("mxCell")),
                 page_background(model) if model is not None else None)]
    out = []
    for i, d in enumerate(diagrams):
        label = d.get("name") or f"page {i + 1}"
        model = d.find("mxGraphModel")
        if model is None and len(d) == 0 and (d.text or "").strip():
            model = _inflate(d)
        if model is None:
            out.append((label, [], None))
        else:
            out.append((label, list(model.iter("mxCell")), page_background(model)))
    return out


def ink_check(png, border, scale, limit_mp=RASTER_LIMIT_MP, cells=None, background=None):
    """('ok' | 'cut' | 'unverified', message) for an exported PNG.

    Runs only on a PNG past `limit_mp`. A complete PNG can end in blank rows when
    a text cell is taller than its text, so under the limit no cut is looked for.

    Past the limit, the page's own cells say where ink must be: every plain box
    with a solid edge or fill must reach its bottom edge in the columns it spans
    (ink_expectations). A cut is a run of at least INK_MIN_CUT_PX of such columns
    whose ink ends INK_SHORT_ROWS (plus one per unit of scale) or more above the
    boxes drawn there. Reading the cells, not the picture alone, keeps an uneven
    bottom, a blank border strip and a tall empty text cell from reading as a cut.
    Ink is any pixel more than INK_TOLERANCE levels from the page background.
    The full-width band is the shape a cut-off export takes: everything
    below some row is blank while the rest of the picture looks complete.
    """
    pil = _load_pil()
    if pil is None:
        return "unverified", ("NOTE: the PNG was not checked for a cut-off, because PIL is "
                              "not installed (pip install pillow). Look at the bottom of "
                              "the image.")
    Image, ImageChops = pil
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(png) as im:
        W, H = im.size
        mp = W * H / 1e6
        if mp <= limit_mp:
            return "ok", ""
        bg = _background(im, background)
        want = ink_expectations(cells or [], W, border, scale, bg)
        if not want:
            return "unverified", (f"NOTE: {png} ({mp:.1f} MP) was not checked for a cut-off: "
                                  "the page has no plain box with a solid edge to measure "
                                  "against. Look at the bottom of the image.")
        got = {}
        try:
            ref = None
            for x0 in range(0, W, INK_STRIP_PX):
                groups = [g for g in range(x0 // INK_GROUP_PX,
                                           min(W, x0 + INK_STRIP_PX) // INK_GROUP_PX)
                          if g in want]
                if not groups:
                    continue
                strip = im.crop((x0, 0, min(W, x0 + INK_STRIP_PX), H)).convert("RGB")
                if ref is None or ref.size != strip.size:
                    ref = Image.new("RGB", strip.size, bg)
                mask = ImageChops.difference(strip, ref).point(
                    lambda v: 255 if v > INK_TOLERANCE else 0)
                for g in groups:
                    gx = g * INK_GROUP_PX - x0
                    box = mask.crop((gx, 0, gx + INK_GROUP_PX, H)).getbbox()
                    got[g] = box[3] - 1 if box else -1
        except MemoryError:
            return "unverified", (f"NOTE: {png} ({mp:.1f} MP) was too large to read for the "
                                  "cut-off check. Look at the bottom of the image.")
    slack = INK_SHORT_ROWS + scale
    # A cut is ONE unbroken run of short column groups. A group that reaches its
    # boxes' bottom ends the run; columns with no box in them do not, so a band
    # across the whole page still reads as one run. Scattered short groups (a
    # few occluded spots) never add up to a cut.
    runs, run = [], []
    for g in sorted(want):
        if got.get(g, -1) < want[g] - slack:
            run.append(g)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    short = max(runs, key=len) if runs else []
    if len(short) * INK_GROUP_PX < INK_MIN_CUT_PX:
        return "ok", ""
    x0, x1 = short[0] * INK_GROUP_PX, (short[-1] + 1) * INK_GROUP_PX
    row = max(got.get(g, -1) for g in short)
    need = min(want[g] for g in short)
    return "cut", (f"in {len(short) * INK_GROUP_PX} columns between x={x0} and x={x1} the ink "
                   f"stops by row {row + 1}, but the boxes there reach row {need:.0f}, in a "
                   f"{W}x{H} image ({mp:.1f} MP, past the measured limit of about "
                   f"{limit_mp:.0f} MP)")


def page_outputs(out, n_pages):
    """One output path per page. A single page keeps `out` unchanged."""
    if n_pages <= 1:
        return [out]
    stem, ext = os.path.splitext(out)
    return [f"{stem}-p{i}{ext}" for i in range(1, n_pages + 1)]


def exported_line(path, index, total, label):
    """The EXPORTED line. Stable format, for tooling that reads it:
        EXPORTED: <path>                          (single page)
        EXPORTED: <path> [page N of M: <label>]   (one line per page)
        EXPORTED: <path> [all M pages]            (a multi-page PDF)
    """
    if total <= 1:
        return f"EXPORTED: {path}"
    if index is None:
        return f"EXPORTED: {path} [all {total} pages]"
    return f"EXPORTED: {path} [page {index} of {total}: {label}]"


def export(binary, src, out, fmt, scale, border, page_index=None, all_pages=False):
    """Run one draw.io export. Returns (ok, detail); detail is the CLI output
    when it failed, so the caller can name the page in the message.

    Measured on draw.io 31.4.5: --page-index is 1-based (0 exits 1);
    an index PAST the last page exits 0 and silently writes the last page again,
    so callers pass only 1..page count. Without --page-index an image export
    writes page 1 only, and a PDF writes page 1 only unless --all-pages is given.
    """
    cmd = [
        binary, "--export",
        "--format", fmt,
        "--output", out,
        "--scale", _fmt_scale(scale),
        "--border", str(border),
    ]
    if page_index is not None:
        cmd += ["--page-index", str(page_index)]
    if all_pages:
        cmd.append("--all-pages")
    # Electron apps need these to run without a sandboxed renderer / GPU.
    cmd += ["--no-sandbox", "--disable-gpu"]
    cmd.append(src)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(out):
        return False, (res.stdout + res.stderr).strip()
    return True, ""


def stale_outputs(out, fmt, keep):
    """Things beside `out` that LOOK like pictures of an earlier export of the same
    source with a different page count: the plain <stem>.<ext>, every
    <stem>-pN.<ext>, their .cut-off copies, and (for a PNG export) the crops
    folders of pages not in `keep`. Used only to NAME them in a NOTE; render.py
    never deletes a page picture it is not writing this run.

    The stem and the extension are the requested output's own, as page_outputs
    builds the page names (`-o pic.image` looks for `pic-pN.image`), matched
    whole, never by a glob, so `<stem>-p1-annotated.png` is not one. A
    `<stem>-pN` picture is left out when a source `<stem>-pN.drawio` sits in the
    same folder: that is the other diagram's own picture. A crops folder is named
    only when it holds render.py's marker."""
    folder, name = os.path.split(out)
    folder = folder or "."
    stem, ext = os.path.splitext(name)
    ours = re.compile("(" + re.escape(stem) + r"(?:-p\d+)?)(?:\.cut-off)?"
                      + re.escape(ext) + "$")
    crops = re.compile("(" + re.escape(stem) + r"(?:-p\d+)?)" + re.escape(CROPS_SUFFIX) + "$")
    keep_real = {os.path.realpath(p) for p in keep}
    kept_pages = {os.path.splitext(os.path.basename(p))[0] for p in keep}
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    others = {n[:-len(".drawio")] for n in names if n.endswith(".drawio")} - {stem}
    found = []
    for n in names:
        p = os.path.join(folder, n)
        m = ours.match(n)
        if m:
            if m.group(1) not in others and os.path.realpath(p) not in keep_real:
                found.append(p)
            continue
        m = crops.match(n) if fmt == "png" else None
        if m and m.group(1) not in kept_pages and m.group(1) not in others \
                and _own_crops_dir(p):
            found.append(p)
    return sorted(found)


def _side_path(path, tag, hidden=False):
    """<dir>/<stem>.<tag><ext>, or the hidden .<stem>.<tag><ext>, beside `path`."""
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    return os.path.join(folder, f"{'.' if hidden else ''}{stem}.{tag}{ext}")


def _remove(path):
    """Remove a file if it is there. True when one was removed."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def suggest_scale(scale, png):
    """A scale that brings this PNG under RASTER_FIT_MP, from its real size."""
    with open(png, "rb") as f:
        head = f.read(24)
    w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    mp = w * h / 1e6
    if mp <= 0:
        return None
    return max(0.05, math.floor(scale * math.sqrt(RASTER_FIT_MP / mp) * 20) / 20)


# The Read tool shows an image at most about 2000 px wide (a 6726 px page came
# back scaled by 3.36, measured), so a 10 pt label on a wide page is a
# few pixels tall. A page past this size is also cut into crops at full size.
CROP_OVER_PX = 2000
# Margin around a crop, in diagram units, so a stroke on the edge is inside it.
CROP_MARGIN = 12
# Pixels added to every side of a crop whatever the scale: at a small scale the
# margin above is a few pixels, less than draw.io's own rounding of the page.
CROP_SLACK_PX = 16
# A page's crops live in <page>.crops/, a folder render.py creates with the
# marker file inside; only a folder holding the marker is ever emptied, and only
# of files named like a crop: crop-NN or crop-NN-<slug>.png (the _slug of a label).
CROPS_SUFFIX = ".crops"
CROPS_MARKER = ".made-by-render-py"
CROP_FILE = r"crop-[0-9]{2,}(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?\.png"
# A crop no more than this much wider or taller than CROP_OVER_PX is read
# shrunk by under a tenth, which is still legible; past it a NOTE says so.
CROP_SHRINK_OK = 1.1
# The rest crops: ink is looked for in cells of CROP_CELL_PX, and the picture is
# read in squares of CROP_TILE_PX to bound memory.
CROP_CELL_PX = 40
CROP_TILE_PX = 1800
# A rest crop, slack included, is at most this many pixels on a side.
CROP_REST_MAX_PX = 1800
# The rest scan counts a pixel as ink past this many levels from the page
# colour: lower than INK_TOLERANCE, so a faint fill or a light shadow counts.
REST_INK_TOLERANCE = 2

LOOK_FOR = (
    "LOOK FOR (no check can see these): an auto-routed edge crossing a box or a "
    "label; arrowheads stacked where several edges end at one point; the reading "
    "order of a Hebrew page (stage A on the right); a count or status the diagram "
    "states about code; the bottom edge of every picture.")


def _slug(text, width=24):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return s[:width].rstrip("-")


def _edge_points(cells):
    """[(edge cell, x, y)] in absolute units: every waypoint and free end point of
    every shown edge. An edge's geometry is relative to its parent, like a vertex's.

    A sourcePoint or targetPoint is counted only when that end is NOT joined to a
    cell: draw.io ignores a terminal point once the terminal exists (Graph's
    getFixedTerminalPoint in the draw.io engine), and
    the editor leaves stale ones behind, so counting them would move every crop."""
    by_id = {c.get("id"): c for c in cells if c.get("id") is not None}
    origin = _origin_resolver(cells)
    out = []
    for e in cells:
        if e.get("edge") != "1" or _hidden(e, by_id):
            continue
        # draw.io draws no edge whose joined end is a cell it does not draw (a hidden
        # cell, or one on a hidden layer), so none of its points count. An end inside
        # a collapsed group is drawn to the group, so it still counts.
        if any(e.get(end) in by_id and _hidden(by_id[e.get(end)], by_id, collapsed=False)
               for end in ("source", "target")):
            continue
        geo = e.find("mxGeometry")
        if geo is None:
            continue
        ox, oy = origin(e.get("parent"))
        points = list(geo.findall("Array[@as='points']/mxPoint"))
        for end in ("source", "target"):
            p = _end_point(e, end)
            if p is not None and e.get(end) not in by_id:
                points.append(p)
        for p in points:
            try:
                out.append((e, ox + float(p.get("x") or 0), oy + float(p.get("y") or 0)))
            except (TypeError, ValueError):
                continue
    return out


def crop_regions(cells, W, H, border, scale):
    """[(label, (left, top, right, bottom))] in PNG pixels: one region per
    top-level stage container (a box holding two or more others), then a header
    band above them and a footer band below them when something is drawn there.

    A stage's region covers its own rectangle plus everything that belongs to it:
    a box inside it or parented to it (a note dragged just outside its border
    still belongs), and the waypoints of an edge parented to it or joining two of
    its boxes. Edge waypoints outside every stage count toward the bands.

    The mapping is the one ink_expectations measured: the content's top-left lands
    at border * scale pixels. Content the file does not place exactly (an edge's
    routed path, a label) can push everything right and down by the picture's
    spare width or height, so each region is widened right and down by that spare
    amount, padded by CROP_MARGIN units plus CROP_SLACK_PX pixels, and clamped.
    """
    by_id = {c.get("id"): c for c in cells if c.get("id") is not None}
    shown = [r for r in _shown_rects(cells) if r[0] is not None]
    if not shown:
        return []
    points = _edge_points(cells)
    xs = [r[1] for r in shown] + [p[1] for p in points]
    ys = [r[2] for r in shown] + [p[2] for p in points]
    xmin, ymin = min(xs), min(ys)
    xmax = max([r[1] + r[3] for r in shown] + [p[1] for p in points])
    ymax = max([r[2] + r[4] for r in shown] + [p[2] for p in points])
    origin = border * scale
    dx = max(0.0, W - (origin + (xmax - xmin) * scale + border + 8))
    dy = max(0.0, H - (origin + (ymax - ymin) * scale + border + 8))

    def same(a, b):                        # one rectangle, give or take a unit
        return all(abs(a[i] - b[i]) <= 1 for i in (1, 2)) and \
            all(abs((a[1 + i] + a[3 + i]) - (b[1 + i] + b[3 + i])) <= 1 for i in (0, 1))

    def inside(a, b):                      # a inside b, a is not b nor b's twin
        return a is not b and not same(a, b) and a[1] >= b[1] - 1 and \
            a[2] >= b[2] - 1 and a[1] + a[3] <= b[1] + b[3] + 1 and \
            a[2] + a[4] <= b[2] + b[4] + 1

    def ancestors(cell):
        seen, cid = set(), cell.get("parent")
        while cid is not None and cid not in seen and cid in by_id:
            seen.add(cid)
            cid = by_id[cid].get("parent")
        return seen

    up = {id(r[0]): ancestors(r[0]) for r in shown}
    twins = {}

    def twin_ids(t):                       # t and every box on t's own rectangle
        if id(t) not in twins:
            twins[id(t)] = {r[0].get("id") for r in shown if r is t or same(r, t)}
        return twins[id(t)]

    def member(o, t):                      # o belongs to stage t
        return o is t or same(o, t) or inside(o, t) or bool(up[id(o[0])] & twin_ids(t))

    def one_each(rs):
        # A draw.io group drawn around a stage has the stage's own rectangle: keep
        # one of each such pair, the one with a label, else the first written.
        kept = []
        for r in rs:
            twin = next((k for k in kept if same(k, r)), None)
            if twin is None:
                kept.append(r)
            elif not _label(twin[0]) and _label(r[0]):
                kept[kept.index(twin)] = r
        return kept

    holders = [r for r in shown if sum(1 for o in shown if inside(o, r)) >= 2]
    top = one_each([r for r in holders if not any(inside(r, o) for o in holders)])
    # A lone frame holding two or more stages is not a stage: use the stages
    # inside it, whatever else (a title, a legend) sits outside the frame.
    # Only when everything else in it sits inside those stages or above or below
    # them; a stage holding sub-boxes beside other content stays one crop.
    while len(top) == 1:
        frame = top[0]
        inner = [r for r in holders if inside(r, frame)]
        nxt = one_each([r for r in inner if not any(inside(r, o) for o in inner)])
        if len(nxt) < 2:
            break
        lo = min(r[2] for r in nxt)
        hi = max(r[2] + r[4] for r in nxt)
        loose = [o for o in shown if member(o, frame) and not same(o, frame)
                 and not any(member(o, n) for n in nxt)
                 and not (o[2] + o[4] <= lo + 1 or o[2] >= hi - 1)]
        # A free arrow (no joined end) inside the frame, beside its stages, is loose
        # content too; an arrow joining stages is not.
        loose += [p for p in points
                  if not p[0].get("source") and not p[0].get("target")
                  and frame[1] <= p[1] <= frame[1] + frame[3] and lo < p[2] < hi
                  and not any(n[1] <= p[1] <= n[1] + n[3] and n[2] <= p[2] <= n[2] + n[4]
                              for n in nxt)]
        if loose:
            break
        top = nxt
    if not top:
        return []
    top.sort(key=lambda r: (r[2], r[1]))

    def px(x0, y0, x1, y1):
        s = CROP_MARGIN * scale + CROP_SLACK_PX
        left = origin + (x0 - xmin) * scale - s
        upper = origin + (y0 - ymin) * scale - s
        right = origin + (x1 - xmin) * scale + s + dx
        lower = origin + (y1 - ymin) * scale + s + dy
        return (max(0, int(left)), max(0, int(upper)),
                min(W, int(math.ceil(right))), min(H, int(math.ceil(lower))))

    def extent(rects, pts):
        return (min([o[1] for o in rects] + [p[1] for p in pts]),
                min([o[2] for o in rects] + [p[2] for p in pts]),
                max([o[1] + o[3] for o in rects] + [p[1] for p in pts]),
                max([o[2] + o[4] for o in rects] + [p[2] for p in pts]))

    regions = []
    staged_ids = set()
    claimed = set()
    for t in top:
        members = [o for o in shown if member(o, t)]
        ids = {o[0].get("id") for o in members}
        staged_ids |= {id(o) for o in members}
        owners = ids | twin_ids(t)
        pts = [p for p in points
               if ({p[0].get("parent")} | ancestors(p[0])) & owners
               or (p[0].get("source") in ids and p[0].get("target") in ids)]
        claimed |= {id(p) for p in pts}
        label = _label(t[0]) or next((_label(o[0]) for o in shown
                                      if same(o, t) and _label(o[0])), "") or "stage"
        regions.append((label, px(*extent(members, pts))))
    first = min(t[2] for t in top)
    last = max(t[2] + t[4] for t in top)
    loose = [p for p in points if id(p) not in claimed]
    for name, keep, keep_pt in (
            ("header", lambda o: o[2] + o[4] <= first + 1, lambda p: p[2] < first),
            ("footer", lambda o: o[2] >= last - 1, lambda p: p[2] > last)):
        band = [o for o in shown if id(o) not in staged_ids and keep(o)]
        pts = [p for p in loose if keep_pt(p)]
        if band or pts:
            regions.append((name, px(*extent(band, pts))))
    return regions


def _png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def crops_dir(png):
    """The folder that holds page `png`'s crops: d-p1.png -> d-p1.crops."""
    return os.path.splitext(png)[0] + CROPS_SUFFIX


def _own_crops_dir(folder):
    """True only for a real folder (not a link) holding render.py's marker file."""
    marker = os.path.join(folder, CROPS_MARKER)
    return (os.path.isdir(folder) and not os.path.islink(folder)
            and os.path.isfile(marker) and not os.path.islink(marker))


def clear_crops(png, drop_empty=True):
    """Remove the crop files from page `png`'s own crops folder: only names of the
    shape write_crops gives (CROP_FILE), only in a folder render.py made (marker),
    never anything else there. With `drop_empty`, a folder left holding only the
    marker goes too. Returns the paths removed."""
    folder = crops_dir(png)
    if not _own_crops_dir(folder):
        return set()
    name = re.compile(CROP_FILE)
    gone = set()
    for n in os.listdir(folder):
        p = os.path.join(folder, n)
        if name.fullmatch(n) and os.path.isfile(p) and not os.path.islink(p) and _remove(p):
            gone.add(p)
    if drop_empty and os.listdir(folder) == [CROPS_MARKER]:
        # Moved aside first, so no unmarked folder is ever left under the page's own
        # name (a run stopped half way would lock render.py out of it).
        aside = tempfile.mkdtemp(prefix="." + os.path.basename(folder) + ".gone-",
                                 dir=os.path.dirname(folder) or ".")
        try:
            os.rmdir(aside)
            os.rename(folder, aside)
        except OSError:
            return gone
        _remove(os.path.join(aside, CROPS_MARKER))
        try:
            os.rmdir(aside)
        except OSError:
            pass
    return gone


def _prepare_crops_dir(png):
    """Page `png`'s crops folder, emptied of its old crops and ready to fill, or
    None (with a NOTE) when that name is taken by something render.py did not
    make: a plain folder without the marker, a file, or a link."""
    folder = crops_dir(png)
    if os.path.lexists(folder) and not _own_crops_dir(folder):
        print(f"NOTE: {folder} exists and was not made by render.py, so no crops were "
              "written; rename it, or pass --no-crops.", file=sys.stderr)
        return None
    if not os.path.lexists(folder):
        # Made under a hidden name with its marker, then renamed: a run stopped half
        # way leaves no unmarked folder of ours under the page's own name.
        made = tempfile.mkdtemp(prefix="." + os.path.basename(folder) + ".new-",
                                dir=os.path.dirname(folder) or ".")
        with open(os.path.join(made, CROPS_MARKER), "w") as f:
            f.write("This folder is made and refilled by draw-diagram's render.py. "
                    "Files named crop-NN....png are replaced on every export.\n")
        try:
            os.rename(made, folder)
        except OSError:
            _remove(os.path.join(made, CROPS_MARKER))
            os.rmdir(made)
            if not _own_crops_dir(folder):
                raise
    clear_crops(png, drop_empty=False)
    return folder


def _sips_crops(png, W, H, targets):
    """Cut `targets` [(path, label, box)] out of `png` with macOS sips. Returns
    [(path, label)] of the crops that came out at the box's size.

    sips gets two kinds of box wrong while exiting 0 (measured on macOS 26):
    an offset of 0,0 is ignored and the crop is taken from the centre, and a box at
    the left edge that reaches the bottom is not cut at all. So the page is first
    padded by 1 px on every side, which puts every offset at 1 or more and keeps every
    box off the far edges; 117 boxes cut that way matched Pillow's crop exactly."""
    fd, padded = tempfile.mkstemp(prefix="render-sips-pad-", suffix=".png")
    os.close(fd)
    done, current = [], None
    try:
        res = subprocess.run(["sips", "--padToHeightWidth", str(H + 2), str(W + 2),
                              png, "--out", padded], capture_output=True, text=True)
        if res.returncode != 0 or _png_size(padded) != (W + 2, H + 2):
            return []
        for path, label, (l, t, r, b) in targets:
            current = _claim(path)
            res = subprocess.run(["sips", "-c", str(b - t), str(r - l), "--cropOffset",
                                  str(t + 1), str(l + 1), padded, "--out", path],
                                 capture_output=True, text=True)
            if not (res.returncode == 0 and _png_size(path) == (r - l, b - t)):
                raise OSError(f"sips did not cut {path}")
            done.append((path, label))
            current = None
    except OSError:
        # Cutting failed part way: every crop this call cut goes, and the
        # caller prints the boxes to cut by hand.
        for path in [d[0] for d in done] + ([current] if current else []):
            _remove(path)
        return []
    finally:
        _remove(padded)
    return done


def _breaks_line(text):
    """True when `text` holds anything str.splitlines() splits at, so one output
    line could not carry it whole."""
    return ("x" + text + "x").splitlines() != ["x" + text + "x"]


def _claim(path):
    """Create `path` as a new empty file and return it, or raise FileExistsError.
    Refuses anything already there, a link included, so a crop is never written
    through a link someone left in the folder, and a failed write never removes a
    file this run did not create."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                 0o644)
    os.close(fd)
    return path


def _rest_regions(png, regions, background, pil):
    """[(label, box)] covering every part of `png` that has ink and lies in none of
    `regions`, so no drawn pixel is left out of every crop (whatever crop_regions
    guessed about the stages). The page is read in CROP_CELL_PX cells; ink is any
    channel more than REST_INK_TOLERANCE levels from the page colour, lower than
    the cut-off check's, so a faint fill or a light shadow counts. A cell wholly
    inside a region is covered. The other ink cells are joined into patches (cells
    touching, corners included), and each patch is one region, so a label across
    a tile line stays whole; only a patch larger than CROP_REST_MAX_PX is cut into
    squares. Every region, slack included, is at most CROP_REST_MAX_PX on a side.
    Labels rest-1, rest-2, ... in reading order."""
    Image, ImageChops = pil
    cell, s = CROP_CELL_PX, CROP_SLACK_PX
    chunk = max(1, (CROP_REST_MAX_PX - 2 * s) // cell)       # cells per side
    scan = cell * max(1, CROP_TILE_PX // cell)                # read in strips this big

    def covered(x0, y0, x1, y1):
        return any(l <= x0 and t <= y0 and r >= x1 and b >= y1 for _n, (l, t, r, b) in regions)

    ink = set()
    with Image.open(png) as im:
        W, H = im.size
        bg = _background(im, background)
        for ty in range(0, H, scan):
            for tx in range(0, W, scan):
                tw, th = min(scan, W - tx), min(scan, H - ty)
                part = im.crop((tx, ty, tx + tw, ty + th)).convert("RGB")
                mask = ImageChops.difference(part, Image.new("RGB", part.size, bg)).point(
                    lambda v: 255 if v > REST_INK_TOLERANCE else 0)
                if mask.getbbox() is None:
                    continue
                for cy in range(0, th, cell):
                    for cx in range(0, tw, cell):
                        x0, y0 = tx + cx, ty + cy
                        x1, y1 = min(W, x0 + cell), min(H, y0 + cell)
                        if covered(x0, y0, x1, y1):
                            continue
                        if mask.crop((cx, cy, cx + (x1 - x0), cy + (y1 - y0))).getbbox():
                            ink.add((x0 // cell, y0 // cell))
    patches, seen = [], set()
    for start in sorted(ink, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        seen.add(start)
        stack, patch = [start], []
        while stack:
            x, y = stack.pop()
            patch.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if n in ink and n not in seen:
                        seen.add(n)
                        stack.append(n)
        patches.append(patch)
    boxes = []
    for patch in patches:
        x0 = min(x for x, _y in patch)
        y0 = min(y for _x, y in patch)
        parts = {}
        for x, y in patch:
            parts.setdefault(((x - x0) // chunk, (y - y0) // chunk), []).append((x, y))
        for part in parts.values():
            boxes.append((max(0, min(x for x, _y in part) * cell - s),
                          max(0, min(y for _x, y in part) * cell - s),
                          min(W, (max(x for x, _y in part) + 1) * cell + s),
                          min(H, (max(y for _x, y in part) + 1) * cell + s)))
    boxes.sort(key=lambda b: (b[1], b[0]))
    return [(f"rest-{i}", box) for i, box in enumerate(boxes, start=1)]


def write_crops(png, cells, scale, border, background=None):
    """Cut the regions of crop_regions() out of `png` at full size, then (with
    Pillow) plain rest crops over any ink none of them holds, into the page's own
    crops folder as crop-NN-<label>.png, after emptying that folder of its older
    crops (_prepare_crops_dir). Pillow when it imports, else macOS `sips`, else a
    NOTE with the pixel boxes to crop by hand. Returns [(path, label)] of the
    crops written. A page no wider and no taller than CROP_OVER_PX gets none (the
    page itself reads at full size), and its older crops are cleared. If cutting
    fails part way, the crops this call wrote are removed and the error raised."""
    # The old crops show the earlier picture, so they go before anything below can
    # fail or stop early (even reading the picture's size); a failure later removes
    # only what this call wrote.
    clear_crops(png, drop_empty=False)
    W, H = _png_size(png)
    if W <= CROP_OVER_PX and H <= CROP_OVER_PX:
        clear_crops(png)
        return []
    # A box with no area (a region mapped wholly past the picture's edge) cannot be cut.
    regions = [(label, box) for label, box in crop_regions(cells, W, H, border, scale)
               if box[2] > box[0] and box[3] > box[1]]
    pil = _load_pil()
    if pil:
        regions = regions + _rest_regions(png, regions, background, pil)
    else:
        print(f"NOTE: without Pillow, the parts of {png} outside the stage crops were not "
              "checked for content; read the page itself for them.", file=sys.stderr)
    if not regions:
        print(f"NOTE: {png} is {W}x{H} px and has no stage container it can crop; "
              "read it in parts (Read shows at most about 2000 px across).",
              file=sys.stderr)
        clear_crops(png)
        return []
    if _breaks_line(crops_dir(png)):
        print(f"NOTE: no crops were written for {png!r}: its path breaks a line, so a "
              "CROP line could not name them; read the page itself.", file=sys.stderr)
        return []
    folder = _prepare_crops_dir(png)
    if folder is None:
        return []
    targets = []
    for i, (label, box) in enumerate(regions, start=1):
        slug = _slug(label)
        path = os.path.join(folder, f"crop-{i:02d}" + (f"-{slug}" if slug else "") + ".png")
        if os.path.lexists(path):
            # Emptying the folder keeps a link or a folder of that name; never cut
            # through it or remove it.
            print(f"NOTE: {path} is not a crop file render.py wrote (a link or a "
                  "folder), so that crop was not cut; move it away.", file=sys.stderr)
            continue
        targets.append((path, label, box))
        bw, bh = box[2] - box[0], box[3] - box[1]
        if max(bw, bh) > CROP_OVER_PX * CROP_SHRINK_OK:
            print(f"NOTE: crop {path} [{label}] is still {bw}x{bh} px; "
                  "Read shows it shrunk, so read it in parts.", file=sys.stderr)
    done, current = [], None
    try:
        if pil:
            Image = pil[0]
            with Image.open(png) as im:
                for path, label, box in targets:
                    current = _claim(path)
                    im.crop(box).save(path)
                    done.append((path, label))
                    current = None
        elif shutil.which("sips"):
            done = _sips_crops(png, W, H, targets)
    except Exception:
        # Only what this call wrote: the folder may hold a person's own files too.
        for path in [d[0] for d in done] + ([current] if current else []):
            _remove(path)
        raise
    missed = [t for t in targets if t[0] not in {d[0] for d in done}]
    if missed:
        print("NOTE: these crops were not cut (no Pillow, and no sips or it failed); "
              f"crop {png} by hand at these pixel boxes (left, top, right, bottom):",
              file=sys.stderr)
        for path, label, box in missed:
            print(f"  {label}: {box}", file=sys.stderr)
    return done


def export_all(binary, src, out, fmt, requested_scale, border, skip_ink_check=False,
               crops=False):
    """Export every page; one EXPORTED line each. Returns how many PNGs are cut off.

    Each picture is written to a hidden .<name>.partial file first and moved to
    its own name only once it exported and passed the cut-off check, so a name
    printed on an EXPORTED line always holds a checked picture of this source.
    A cut-off PNG gets no EXPORTED line: it is kept as <name>.cut-off.png for
    looking, and an older picture under <name> is removed, since it no longer
    matches the source.

    Every page is exported before any picture is replaced: when one page fails,
    all the .partial files go and no picture on disk changes (exit 2, naming the
    page). A PNG page's crops live in its own <page>.crops folder, which only
    write_crops and clear_crops touch. Pictures from an earlier export with a
    different page count are never deleted: after a run that succeeded, a NOTE
    names them.
    """
    pages = export_pages(ET.parse(src).getroot())
    total = len(pages)
    outputs = page_outputs(out, total)
    # Tooling reads the EXPORTED line line by line, so it must hold the whole path.
    if any(_breaks_line(p) for p in [out] + list(outputs)):
        print(f"EXPORT FAILED: the output path {out!r} breaks a line, so an EXPORTED "
              "line could not name it. No picture was changed; export into another "
              "folder.", file=sys.stderr)
        sys.exit(2)

    made = set()

    def finish(success):
        # render.py never deletes a page picture it is not writing this run; after a
        # run that succeeded, a NOTE names what looks like an older page count's.
        if not success:
            return
        left = stale_outputs(out, fmt, keep=made)
        if left:
            print("NOTE: these look like pictures from an older export of this diagram "
                  "with a different page count; they were left alone, so remove them by "
                  "hand if they are stale:\n" + "\n".join(f"  {p}" for p in left),
                  file=sys.stderr)

    if fmt == "pdf":
        scale = requested_scale if requested_scale is not None else DEFAULT_SCALE
        tmp = _side_path(out, "partial", hidden=True)
        _remove(tmp)
        ok, detail = export(binary, src, tmp, fmt, scale, border, all_pages=total > 1)
        if not ok:
            _remove(tmp)
            print(f"EXPORT FAILED: {out} (all {total} pages): draw.io reported\n{detail}\n"
                  "  No picture was changed.", file=sys.stderr)
            sys.exit(2)
        os.replace(tmp, out)
        made.add(out)
        finish(True)
        print(exported_line(out, None, total, None))
        return 0
    # Phase 1: every page to its own hidden .partial file. One failure removes
    # them all and changes no picture on disk, so a set is never half new.
    written = []
    for i, ((label, cells, background), path) in enumerate(zip(pages, outputs), start=1):
        if fmt == "png":
            scale, note = choose_scale(cells, requested_scale, border,
                                       page=label if total > 1 else None)
            if note:
                print(note, file=sys.stderr)
        else:
            scale = requested_scale if requested_scale is not None else DEFAULT_SCALE
        tmp = _side_path(path, "partial", hidden=True)
        _remove(tmp)
        ok, detail = export(binary, src, tmp, fmt, scale, border,
                            page_index=i if total > 1 else None)
        if not ok:
            for p in [tmp] + [w[3] for w in written]:
                _remove(p)
            where = f"page {i} of {total} ('{label}')" if total > 1 else "the page"
            print(f"EXPORT FAILED: {where} of {src} did not export: draw.io reported\n"
                  f"{detail}\n  No picture was changed.", file=sys.stderr)
            sys.exit(2)
        written.append((i, label, path, tmp, scale, cells, background))
    # Phase 2: each page is checked, then takes its name.
    cut = 0
    notes_shown = set()
    cut_copies = []
    for i, label, path, tmp, scale, cells, background in written:
        cut_path = _side_path(path, "cut-off")
        status, msg = "ok", ""
        if fmt == "png" and not skip_ink_check:
            status, msg = ink_check(tmp, border, scale, cells=cells, background=background)
        if status == "cut":
            cut += 1
            os.replace(tmp, cut_path)
            cut_copies.append(cut_path)
            made.add(cut_path)
            old = _remove(path)
            clear_crops(path)                    # they show the earlier picture
            better = suggest_scale(scale, cut_path)
            if requested_scale is None:
                fix = f"Pass --scale {_fmt_scale(better)} and export again."
            else:
                fix = (f"Leave --scale out to have it chosen, or pass --scale "
                       f"{_fmt_scale(better)}, and export again.")
            where = f"page {i} of {total} ('{label}')" if total > 1 else "the page"
            print(f"EXPORT FAILED: {where} is cut off: {msg}.\n"
                  f"  draw.io drops the bottom of a PNG past about {RASTER_LIMIT_MP:.0f} MP "
                  f"while still reporting success. {fix}\n"
                  f"  The cut-off picture is kept as {cut_path} for looking"
                  + (f"; the older {path} was removed, since it showed an earlier version"
                     if old else "") + ".", file=sys.stderr)
            continue
        os.replace(tmp, path)
        _remove(cut_path)
        made.add(path)
        print(exported_line(path, i, total, label))
        cut_crops = []
        if fmt == "png" and crops:
            try:
                cut_crops = write_crops(path, cells, scale, border, background=background)
            except Exception as e:          # a crop never costs the export
                cut_crops = []
                print(f"NOTE: the crops of {path} were not cut ({type(e).__name__}: {e}); "
                      "the page itself exported fine.", file=sys.stderr)
            for crop_path, crop_label in cut_crops:
                # A tab before the label: a reader splits at the LAST tab, so neither
                # a folder name nor the label can cut the path short.
                print(f"CROP: {crop_path}\t[{crop_label.replace(chr(9), ' ')}]")
        elif fmt == "png":
            clear_crops(path)                    # --no-crops: they show an older picture
        if status == "unverified" and msg not in notes_shown:
            print(msg, file=sys.stderr)
            notes_shown.add(msg)
    # A cut-off page fails the run (exit 2), so the older pictures stay until a
    # run succeeds.
    finish(not cut)
    if skip_ink_check and fmt == "png":
        print("NOTE: --skip-ink-check given, so the PNG was not checked for a cut-off. "
              "Look at the bottom of the image.", file=sys.stderr)
    return cut


def build_parser():
    ap = argparse.ArgumentParser(description="Validate and render a .drawio file.")
    ap.add_argument("file", help="path to the .drawio source")
    ap.add_argument("-f", "--format", default="png", choices=["png", "svg", "pdf"])
    ap.add_argument("-o", "--output", help="output path (default: alongside source); a "
                    "multi-page file writes <name>-p1, <name>-p2, ... for PNG and SVG")
    ap.add_argument("--scale", default=None, type=float,
                    help="render scale (default: 2, lowered for a large page so the PNG "
                         "is not cut off; an explicit value is always used)")
    ap.add_argument("--border", default=10, type=int, help="border px (default 10)")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--skip-ink-check", action="store_true",
                    help="do not check the PNG for a cut-off bottom (prints a NOTE)")
    ap.add_argument("--no-crops", action="store_true",
                    help="do not cut a page wider or taller than 2000 px into crops")
    return ap


def main():
    args = build_parser().parse_args()

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
    cut = export_all(binary, args.file, out, args.format, args.scale, args.border,
                     skip_ink_check=args.skip_ink_check, crops=not args.no_crops)
    print(LOOK_FOR)
    if cut:
        sys.exit(2)


if __name__ == "__main__":
    main()
