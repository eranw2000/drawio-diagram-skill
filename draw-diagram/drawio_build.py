#!/usr/bin/env python3
"""Shared helpers for scripts that generate draw.io files.

It holds the pieces such a script needs: a label escape, a box height estimate,
column stacking, right-to-left wrappers and an edge writer. Import it:

    import sys, os
    sys.path.insert(0, os.path.expanduser("~/.claude/skills/draw-diagram"))
    from drawio_build import Diagram, rich, rtl, path

    d = Diagram("Flow")
    d.box("hdr", rich("**Title**"), 0, 0, 600, style=TEXT)
    bottom = d.stack([("a", rich("First step")), ("b", rich("Second step"))], 0, 80, 300)
    d.edge("e1", "a", "b", side="bottom")
    d.write("flow.drawio")            # writes, then runs render.py --validate-only

What it guarantees, so a generator does not have to:
  - a label is HTML, XML-escaped once at the single point a cell is written (attr),
    and its style gets html=1, without which draw.io prints the tags as text;
  - a style is written with each key once, the LAST value winning as in draw.io,
    so render.py's checks read the same value draw.io paints;
  - a box wraps its text (whiteSpace=wrap) unless its style says otherwise;
  - a box with no height gets est_height(), the same estimate render.py's height
    check makes, plus a margin, so a box sized here does not warn there; a label
    that sets its own font size is refused, since no estimate can size it;
  - a container with no height grows, when the file is written, to hold what was
    put in it (and widens if a child runs past its right side);
  - a child names a parent that already exists, so every container is written
    before its contents (draw.io paints in document order);
  - an edge names two cells that exist and a parent that exists, or the file is
    not written;
  - edges leaving one box on one side get their own exit points, spread along it,
    and a spread point never sits on a hand-set exit of the same box (it moves).
Standard library only, Python 3.9.
"""
import hashlib
import html
import importlib.util
import math
import os
import re
import subprocess
import sys


def _load_render():
    """The render.py beside the REAL file of this module, loaded under a private
    name. A plain `import render` takes whatever module of that name the process
    already holds, and through a symlink it looks beside the link."""
    here = os.path.dirname(os.path.realpath(__file__))
    src = os.path.join(here, "render.py")
    name = "_drawio_build_render_" + hashlib.sha1(src.encode()).hexdigest()[:12]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        del sys.modules[name]
        raise
    return mod


_render = _load_render()  # the checks this module sizes against

# The defaults of render.check_text_height, so a box sized here passes that check.
CHAR_EM = 0.5
LINE_EM = 1.35
MARGIN = 12          # the small padding bump on every box
SIDES = ("right", "left", "top", "bottom")
_SPACE = r"(?:\s|&nbsp;)"
# Other spellings of a no-break space, read as &nbsp; so render.py's line split
# blanks them too (it only knows &nbsp;); otherwise a row of one is counted twice.
_NBSP = re.compile(r"&#160;|&#x0*a0;|\u00a0", re.I)
# A row with nothing visible on it still takes a line of height: <br>&nbsp;<br>,
# or an empty block such as <div><br></div> (what draw.io writes for an empty line).
_BLANK = re.compile(r"<br\s*/?>" + _SPACE + r"*(?=<br\s*/?>)", re.I)
_EMPTY_BLOCK = re.compile(r"<(div|p)\b[^>]*>(?:" + _SPACE + r"|<br\s*/?>)*</\1\s*>", re.I)
# A font size set by the label's own HTML: any font-size: or font: shorthand INSIDE
# a tag, however its attribute is quoted, or <font size=>. No estimate here can
# size it. Text that only mentions font-size is never inside a tag, since rich()
# escapes its <.
_INLINE_FONT = re.compile(r"<[a-z][^>]*\bfont(?:-size)?\s*:|<font\b[^>]*\bsize\s*=", re.I)


def attr(text):
    """A label (real HTML: <b>, <br>) escaped once for an XML attribute."""
    return html.escape(text, quote=True)


def rich(text):
    """Light markup to label HTML: **bold**, `code`, and newlines as <br>. The text
    itself is HTML-escaped first, so a literal < or & shows as written."""
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<font face='Courier New'>\1</font>", s)
    return s.replace("\n", "<br>")


def rtl(label):
    """A Hebrew label: base direction right to left (render.py warns without it)."""
    return '<div dir="rtl" style="text-align:right">' + label + '</div>'


def path(t):
    """A file path inside a Hebrew label, pinned left to right so its parts keep
    their order. Pass &lt; and &gt; for literal angle brackets."""
    return ('<span dir="ltr" style="font-family:Courier New;unicode-bidi:embed">'
            + t + '</span>')


def est_height(label, w, style=""):
    """Height for a box of width `w` holding `label`, by render.py's own estimate:
    each line wraps at CHAR_EM * fontSize per character, each visual line costs
    LINE_EM * fontSize, a cylinder or document adds what its outline takes, and
    the top and bottom spacing plus MARGIN go on. Unlike render.py's check, a
    blank row counts: it takes a line of height in the picture.

    draw.io's spacing keys "add padding around the text", and `spacing` is the
    Global value beside Top, Left, Bottom and Right in the Text tab (style
    reference and text-styles pages, drawio.com/docs), so it goes on every side.
    The width also loses spacingTop, as render.py's check takes it off there.

    A label that sets its own font size (font-size: or <font size=) is refused:
    the estimate only knows the style's fontSize. Put the size in the style, or
    pass a height."""
    if _INLINE_FONT.search(label):
        raise ValueError("the label sets its own font size (a font-size in a style= attribute, "
                         "or <font size=>), which est_height cannot size: put fontSize in the "
                         "style, or pass h")
    style = _style_str(style)
    flags, kv = _render._style({"style": style})
    size = float(kv.get("fontSize", 12))
    every = float(kv.get("spacing", 0))
    pad = {k: float(kv.get(k, 0)) + every
           for k in ("spacingLeft", "spacingRight", "spacingTop", "spacingBottom")}
    usable = max(w - pad["spacingLeft"] - pad["spacingRight"] - pad["spacingTop"] - 8.0, 20.0)
    # Empty blocks are counted, then replaced by a bare block end, which still
    # splits the text on either side into two rows but is not a <br> to count again.
    label = _NBSP.sub("&nbsp;", label)
    blank = len(_EMPTY_BLOCK.findall(label))
    rest = _EMPTY_BLOCK.sub("</p>", label)
    blank += len(_BLANK.findall(rest))
    lines = [ln for ln in _render._lines({"value": rest}) if ln]
    visual = sum(max(1, math.ceil(len(ln) * size * CHAR_EM / usable)) for ln in lines)
    visual = max(visual + blank, 1)
    lost = _render.SHAPE_LOST_HEIGHT.get(_render._shape_name(flags, kv), 0.0)
    return int(math.ceil(visual * size * LINE_EM + lost
                         + pad["spacingTop"] + pad["spacingBottom"] + MARGIN))


def _style_str(*styles, defaults=None, **force):
    """Styles joined into one with each key ONCE, and nothing else moved. draw.io
    applies the parts in order (a named style such as `ellipse` or `text` overrides
    what came before it), and a leading ';' means "no default style", so both the
    order and that ';' are kept. Of a repeated key only the LAST copy stays, in its
    own place, so a check that takes the first match reads what draw.io paints.
    `defaults` go FIRST, only for keys no style sets, so anything written wins over
    them; `force` goes LAST and replaces every copy of its keys."""
    reset = bool(styles) and (styles[0] or "").lstrip().startswith(";")
    parts = [p.strip() for st in styles for p in (st or "").split(";") if p.strip()]

    def key(p):
        return p.split("=", 1)[0].strip() if "=" in p else None

    last = {key(p): i for i, p in enumerate(parts) if key(p)}
    kept = [p for i, p in enumerate(parts)
            if key(p) is None or (last[key(p)] == i and key(p) not in force)]
    head = [f"{k}={v}" for k, v in (defaults or {}).items() if k not in last and k not in force]
    tail = [f"{k}={v}" for k, v in force.items()]
    body = "".join(p + ";" for p in head + kept + tail)
    return (";" if reset else "") + body


def _exit_point(style):
    """The (exitX, exitY) a style sets by hand, or None. Offsets and exitPerimeter
    are left out on purpose: a spread point keeps clear of the x,y whatever they
    say, which costs at most a slightly uneven spread and never a shared trunk."""
    _f, kv = _render._style({"style": style})
    try:
        return (float(kv["exitX"]), float(kv["exitY"]))
    except (KeyError, ValueError):
        return None


class Diagram:
    """One page. Add cells in drawing order; write() refuses a broken graph."""

    def __init__(self, name="Page-1", page_id="page-1"):
        self.name, self.page_id = name, page_id
        self.vertices, self.edges = [], []
        self.geo = {}                    # id -> (x, y, w, h), absolute for parent "1"
        self._fit = {}                   # container id -> (pad, height its label needs)

    def _new_id(self, cid):
        if not cid or cid in ("0", "1") or cid in self.geo or any(e["id"] == cid for e in self.edges):
            raise ValueError(f"cell id {cid!r} is empty, reserved or already used")

    def box(self, cid, label, x, y, w, h=None, style="", parent="1"):
        """A vertex. Returns its height (computed when `h` is None). A `parent` other
        than the layer must already exist, so containers come before contents."""
        self._new_id(cid)
        if parent != "1" and parent not in self.geo:
            raise ValueError(f"box {cid!r}: parent {parent!r} must be added before it")
        style = _style_str(style, defaults={"whiteSpace": "wrap"}, html="1")
        h = est_height(label, w, style) if h is None else h
        self.vertices.append({"id": cid, "label": label, "style": style, "parent": parent,
                              "x": x, "y": y, "w": w, "h": h})
        self.geo[cid] = (x, y, w, h)
        return h

    def container(self, cid, label, x, y, w, h=None, style="", parent="1", pad=20):
        """A box that holds others. With no `h` it grows, when the file is written,
        to `pad` below its lowest child (and `pad` past its widest one), so a child
        that grows can never spill out of it. Returns its height so far."""
        got = self.box(cid, label, x, y, w, h, style=style, parent=parent)
        if h is None:
            self._fit[cid] = (pad, got)
        return got

    def _fit_containers(self):
        """Size each growing container from its children, innermost first."""
        parent = {v["id"]: v["parent"] for v in self.vertices}
        by_id = {v["id"]: v for v in self.vertices}

        def depth(cid):
            n = 0
            while parent[cid] != "1":
                cid, n = parent[cid], n + 1
            return n

        for cid in sorted(self._fit, key=depth, reverse=True):
            pad, label_h = self._fit[cid]
            x, y, w, h = self.geo[cid]
            kids = [self.geo[v["id"]] for v in self.vertices if v["parent"] == cid]
            h = max([label_h] + [ky + kh + pad for _kx, ky, _kw, kh in kids])
            w = max([w] + [kx + kw + pad for kx, _ky, kw, _kh in kids])
            by_id[cid]["h"], by_id[cid]["w"] = h, w
            self.geo[cid] = (x, y, w, h)

    def stack(self, items, x, y, w, gap=16, style="", parent="1"):
        """Boxes in a column from a running total: items are (id, label) or
        (id, label, style). Returns the y just below the last box."""
        for item in items:
            cid, label = item[0], item[1]
            h = self.box(cid, label, x, y, w, style=item[2] if len(item) > 2 else style,
                         parent=parent)
            y += h + gap
        return y - gap if items else y

    def edge(self, cid, source, target, style="", label="", side=None, entry=None,
             points=(), parent="1"):
        """An arrow from `source` to `target`. `side` ('right', 'left', 'top',
        'bottom') gives it its own exit point on that side of the source; `entry`
        is an (x, y) entry point on the target; `points` are waypoints."""
        self._new_id(cid)
        if side is not None and side not in SIDES:
            raise ValueError(f"edge {cid!r}: side must be one of {SIDES}")
        if label:
            style = _style_str(style, defaults={"labelBackgroundColor": "#ffffff"}, html="1")
        else:
            style = _style_str(style)
        self.edges.append({"id": cid, "source": source, "target": target, "style": style,
                           "label": label, "side": side, "entry": entry,
                           "points": list(points), "parent": parent})

    def _exits(self):
        """(edge id) -> 'exitX=..;exitY=..;' spreading each source side's edges."""
        groups = {}
        for e in self.edges:
            if e["side"]:
                groups.setdefault((e["source"], e["side"]), []).append(e["id"])
        # A hand-set exit is never moved, so the spread keeps clear of it: with n
        # edges on a side it tries n points, then n+1, n+2 ... evenly placed, and
        # takes the first set with no point on a hand-set exit of that box.
        hand = {}
        for e in self.edges:
            pt = None if e["side"] else _exit_point(e["style"])
            if pt is not None:
                hand.setdefault(e["source"], []).append(pt)
        corner = {"right": lambda p: (1, p), "left": lambda p: (0, p),
                  "top": lambda p: (p, 0), "bottom": lambda p: (p, 1)}
        out = {}
        for (src, side), ids in groups.items():
            taken = hand.get(src, [])
            for slots in range(len(ids), len(ids) + 1000):
                pts = [corner[side](round((i + 1) / (slots + 1), 3)) for i in range(len(ids))]
                if not any(abs(a - c) < 1e-6 and abs(b - d) < 1e-6
                           for a, b in pts for c, d in taken):
                    break
            else:
                raise ValueError(f"no free exit points on the {side} of {src!r}")
            for eid, (x, y) in zip(ids, pts):
                out[eid] = f"exitX={x};exitY={y};exitDx=0;exitDy=0;"
        return out

    def xml(self):
        """The whole file as a string. Raises ValueError on an edge whose end or
        parent is not a cell of this page. Growing containers are sized here, so
        call it after the last box."""
        for e in self.edges:
            for end in ("source", "target"):
                if e[end] not in self.geo:
                    raise ValueError(f"edge {e['id']!r}: {end} {e[end]!r} is not a box on this page")
            if e["parent"] != "1" and e["parent"] not in self.geo:
                raise ValueError(f"edge {e['id']!r}: parent {e['parent']!r} is not a box on this page")
        self._fit_containers()
        exits = self._exits()
        rows = []
        for v in self.vertices:
            rows.append(f'<mxCell id="{attr(v["id"])}" value="{attr(v["label"])}" '
                        f'style="{attr(v["style"])}" vertex="1" parent="{attr(v["parent"])}">'
                        f'<mxGeometry x="{v["x"]}" y="{v["y"]}" width="{v["w"]}" '
                        f'height="{v["h"]}" as="geometry"/></mxCell>')
        for e in self.edges:
            entry = ("" if e["entry"] is None else
                     f"entryX={e['entry'][0]};entryY={e['entry'][1]};entryDx=0;entryDy=0;")
            st = _style_str(e["style"], exits.get(e["id"], ""), entry)
            pts = "".join(f'<mxPoint x="{a}" y="{b}"/>' for a, b in e["points"])
            way = f'<Array as="points">{pts}</Array>' if pts else ""
            rows.append(f'<mxCell id="{attr(e["id"])}" value="{attr(e["label"])}" '
                        f'style="{attr(st)}" edge="1" parent="{attr(e["parent"])}" '
                        f'source="{attr(e["source"])}" target="{attr(e["target"])}">'
                        f'<mxGeometry relative="1" as="geometry">{way}</mxGeometry></mxCell>')
        right = max([x + w for x, _y, w, _h in self.geo.values()] or [0]) + 40
        bottom = max([y + h for _x, y, _w, h in self.geo.values()] or [0]) + 40
        body = "\n        ".join(rows)
        return (f'<mxfile host="app.diagrams.net" agent="drawio_build">\n'
                f'  <diagram id="{attr(self.page_id)}" name="{attr(self.name)}">\n'
                f'    <mxGraphModel grid="0" page="1" pageWidth="{int(right)}" '
                f'pageHeight="{int(bottom)}">\n      <root>\n'
                f'        <mxCell id="0"/>\n        <mxCell id="1" parent="0"/>\n'
                f'        {body}\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n')

    def write(self, out, check=True):
        """Write the file; with `check`, run render.py --validate-only on it and
        print what it says. Returns render.py's exit status (0 when not run)."""
        text = self.xml()
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        if not check:
            return 0
        res = subprocess.run([sys.executable, _render.__file__, out, "--validate-only"],
                             capture_output=True, text=True)
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        return res.returncode
