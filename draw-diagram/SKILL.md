---
model: opus
name: draw-diagram
description: Produce a diagram as a draw.io (.drawio) file, then validate and render it to PNG/SVG so it can be visually checked before reporting done. Covers architecture, data flow, sequence, decision tree, system map, ER diagram, org chart, network topology, state machine, infrastructure layout, and workflow automation flows such as Power Automate. Use whenever a diagram is needed for any project (draw.io is the default over Mermaid / Graphviz / ASCII), or when the user says "draw a diagram", "make a flowchart", "diagram this", "render the .drawio", or references app.diagrams.net.
---

# Draw a draw.io diagram

draw.io is the default diagram tool for every project. This skill is the *how*: write the `.drawio` XML using the established visual grammar, then validate and render it so you actually see the result before calling it done.

## 1. Load the grammar (do not duplicate it here)

The rules and the full visual grammar live in `DRAWIO_DIAGRAMS.md`, next to this file: a checklist first, then each rule with the case that taught it, then the shape vocabulary, the seven-color palette and the layout skeleton (header, stages, data store column, footer). **Read that file first** and follow it. Do not reinvent shape semantics or copy the grammar into this skill.

Key points from it (reminders, not a replacement for reading it):
- Save the `.drawio` source alongside any rendered PNG/SVG, inside the repo or project folder it documents, so it stays editable.
- Single wide page (2400+ px), header strip of 3 to 5 boxes, stage containers left-to-right, data-store column on the right, footer notes.
- Reuse the palette for groupings/containers even when using built-in shape libraries (`aws4`, `mscae`, `azure`, `gcp`, `cisco`) for domain icons.
- When a diagram in the same family already exists, copy it and rewrite the contents rather than starting from scratch.

## 2. Write the XML

Manage cell IDs carefully (every `mxCell` needs a unique id; edges reference `source`/`target` ids) and escape `&`, `<`, `>` in labels.

Up to about ten boxes, write the XML by hand. Past that, or for any diagram that will be edited again, write a small generator script instead: it computes each box's height from its text and stacks each column from a running total, so an edit to one label moves everything under it rather than spilling text over the next box. Keep the generator beside the `.drawio` and run it again for every change. The grammar's layout sections say the same.

Build the generator on `drawio_build.py`, next to this file, rather than writing its own escape, height guess or edge writer. It escapes every label once where the cell is written, sizes a box with no height by the same estimate `render.py` checks (blank lines and cylinder or document outlines included), marks every label as HTML, refuses a child added before its container and an edge whose end is not a box, grows a container given no height to hold what is put in it, gives edges leaving one side of a box their own exit points, and runs `render.py --validate-only` after writing:

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/skills/draw-diagram"))
from drawio_build import Diagram, rich

d = Diagram("Flow")
d.container("stage-a", rich("**A. Intake**"), 0, 0, 420,          # no height: it grows to fit
            style="rounded=0;fillColor=#fafafa;strokeColor=#0050b3;dashed=1;verticalAlign=top;")
box = "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
d.stack([("read", rich("**Read** the request")),
         ("check", rich("**Check** it against the rules\n\nRejects go back to the sender."))],
        20, 40, 380, style=box, parent="stage-a")
d.edge("e1", "read", "check", style="endArrow=classic;html=1;", side="bottom")
sys.exit(d.write("flow.drawio"))
```

`rich()` turns `**bold**`, `` `code` `` and newlines into label HTML; `rtl()` and `path()` are the Hebrew wrappers from the grammar.

## 3. Validate and render (this is the point of the skill)

Never report a diagram done without rendering it and looking at the image. Run:

```
python3 ~/.claude/skills/draw-diagram/render.py <path/to/file.drawio>
```

This validates the XML is well-formed and a real draw.io document (prints page/node/edge counts), then exports a PNG next to the source. A file with several pages exports every page, as `<name>-p1.png`, `<name>-p2.png` and so on, with one `EXPORTED: <path> [page N of M: <label>]` line per page; a PDF holds all pages in one file. Read every page, not only the first.

**A PNG page wider or taller than 2000 px is also cut into crops**, because the Read tool shows an image at most about 2000 px across: a 6,726 px page comes back shrunk 3.4 times, and its 10 pt labels are a few pixels tall. There is one crop per top-level stage container, then one for the header band and one for the footer band, cut at full size into the page's own folder `<page>.crops/` as `crop-01-<label>.png` and so on, with one `CROP: <path>` line each and the label after a tab. A crop also covers what belongs to its stage but sits outside its border: a note parented to it, an edge routed out and back. A frame holding two or more stages is not a stage when everything else in it sits above or below them; then the stages inside it are used, even when a title sits outside the frame. Any part of the picture that has ink, even a faint fill, and is in none of these crops gets a plain `rest-N` crop, one per patch of ink and at most 1800 px, so nothing drawn is left out; without Pillow a `NOTE` says that part was not checked. A crop still much wider or taller than 2000 px (a page-wide header band) gets a `NOTE` to read it in parts. render.py creates each crops folder with a marker file inside, and every export empties only crop files from a folder carrying that marker before cutting new ones, so every crop on disk shows the current picture. Anything else in the folder, and any folder or link without the marker, is never touched. Pictures of an older page count are never deleted automatically: after an export whose page count changed, a `NOTE` names the older pages and crops folders to remove by hand. Only this export's own page picture is replaced, or removed when the new one comes out cut off. Cropping uses Pillow, else `sips` on macOS, else a `NOTE` prints the pixel boxes to crop by hand. A large page with no stage containers is cut into rest crops.

Every export ends with a `LOOK FOR` line naming what no check can see: an auto-routed edge crossing a box or label, arrowheads stacked at one point, the reading order of a Hebrew page, a count or status the diagram states about code, and the bottom edge of every picture.

Nine advisory checks run with the validation. None of them changes the exit code, and none replaces looking at the image:
- **Partial box overlaps**, per page, in absolute coordinates. Containment is fine (a stage holding its boxes); partial overlap is the bug, because it renders as text printed over text. Icons deliberately laid over a node are skipped.
- **Cells that cannot wrap**, where the label looks wider than the cell. Any cell without `whiteSpace=wrap` runs its prose straight past the cell edge while validation and export both succeed. This covers shape cells as well as `text;` ones, because the boxes carrying body copy (legends, notes, footers) are ordinary rectangles.
- **Cells whose text needs more height than the box**, the vertical counterpart. This is the one that bites when you EDIT a diagram: add a sentence to an existing note, the box keeps its authored height, and the extra lines render through the bottom border. The overlap check stays silent there because the BOXES do not overlap, only the spilled text does. A cylinder (`shape=cylinder3`) loses about 46px to its caps and a `shape=document` about 28px to its wavy foot, so text that fits a plain box of the same height is flagged there, and the warning names the shape.
- **Edge ends that name no cell**: an end with no `source`/`target` id and no `sourcePoint`/`targetPoint`, or an id that is not a cell on the same page. draw.io draws nothing for it while the edge count still includes it. A legend arrow written with both points is fine.
- **Edges in different colours leaving one box at the same exit point**. They share one trunk and the last one drawn covers the others, so the picture shows one colour where the diagram means several. Give each its own `exitX`/`exitY`. A same-colour fan (a bus that splits) is fine.
- **A box hidden under a filled box painted after it**: draw.io paints in document order, children with their parent, so a band or container written after the boxes it holds covers them completely. Write the container first, or make it their parent. Unfilled shapes and image overlays are skipped.
- **A Hebrew line that starts or ends with a Latin run** (`.xlsx`, a file name, a code word) in a label that sets no direction. The base direction is left-to-right unless the label says otherwise, so the run renders at the wrong end and a Hebrew reader takes `.xlsx` for the first word. Wrap a sentence in `<div dir="rtl">` and a file path in `<span dir="ltr">`; either one silences it, and so does an all-Hebrew line.
- **Label text with an em dash, a curly quote, or a box-drawing character.** A diagram written from a script never goes through an editor's or CI's prose checks, so this is where its prose is checked.
- **Compressed pages**: the cell-level checks read uncompressed files, so turn off Extras > Compressed in the app and re-save.

Both text checks split a label on `<div>`, `<p>` and `<li>` as well as `<br>`, because the draw.io UI emits block tags rather than line breaks, and a `<br>`-only split reads a multi-line label as one very long line.

Before reporting the diagram done, the run should show zero `WARNING` lines, or you name the one that is deliberate and why.

**The PNG size is checked, and a cut-off PNG is an export failure (exit 2).** draw.io stops drawing a PNG past about 32 MP (measured on draw.io 31.4.5) while still reporting success, so the bottom of a large page comes out blank. Without `--scale`, the scale starts at 2 and is lowered per page to keep the PNG under 30 MP (a `NOTE` on stderr says so). An explicit `--scale` is always used; past the limit the PNG is read, and the export fails when one unbroken run of at least 64 px of columns has ink ending well above the boxes the page draws there (a box with a solid edge or fill says how low its columns must reach, so an uneven bottom or an empty note is not a cut). This check needs Pillow; without it a `NOTE` says the PNG was not checked.

Options:
- `--format svg` (or `pdf`) for a different output
- `-o <path>` for an explicit output location (a multi-page file adds `-p1`, `-p2`, ... to it)
- `--validate-only` to skip the export (well-formedness, counts, and every advisory check)
- `--scale` / `--border` to tune the raster (leave `--scale` out to have it chosen per page)
- `--skip-ink-check` to skip the cut-off check on purpose (prints a `NOTE`)
- `--no-crops` to skip the crops (also empties the page's own crops folder)

Then **Read every crop, then the page**, for each page: the crops show the detail at full size, the page shows how the parts fit together. Check what the `LOOK FOR` line names, plus readable labels, edges joining the right boxes, and the palette applied consistently. Fix the XML and re-render until it is right. Do not claim it is done blind, the same way you would not claim a UI change works without loading the page.

**When you ask a helper agent for a diagram, the brief must tell it to Read every crop and page it renders and to say which ones it read.**

### Renderer dependency

The export step needs the draw.io desktop engine. `render.py` finds it automatically on `PATH` or in the usual install locations on macOS, Linux, and Windows. If it is missing, the script still validates and prints the install command for your platform.

Validation always works with no dependencies (pure Python standard library), so a missing renderer never blocks producing a valid, editable `.drawio` file. It only blocks the visual check.

On a headless Linux box the draw.io desktop binary needs a virtual display. Run the exporter under `xvfb-run` if it fails there.

## 4. Report

State where you saved the `.drawio` source and the rendered image, and confirm you visually verified the render.
