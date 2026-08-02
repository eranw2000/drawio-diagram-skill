---
model: opus
name: draw-diagram
description: Produce a diagram as a draw.io (.drawio) file, then validate and render it to PNG/SVG so it can be visually checked before reporting done. Covers architecture, data flow, sequence, decision tree, system map, ER diagram, org chart, network topology, state machine, infrastructure layout, and workflow automation flows. Use whenever a diagram is needed for any project (draw.io is the default over Mermaid / Graphviz / ASCII), or when the user says "draw a diagram", "make a flowchart", "diagram this", "render the .drawio", or references app.diagrams.net.
---

# Draw a draw.io diagram

draw.io is the default diagram tool for every project. This skill is the *how*: write the `.drawio` XML using the established visual grammar, then validate and render it so you actually see the result before calling it done.

## 1. Load the grammar (do not duplicate it here)

The full visual grammar lives in `DRAWIO_DIAGRAMS.md`, next to this file: shape vocabulary, the seven-color palette, the layout skeleton (header, stages, data store column, footer), arrow conventions, and the decision-hub temporal-ordering defenses. **Read that file first** and follow it. Do not reinvent shape semantics or copy the grammar into this skill.

Key points from it, as reminders rather than a replacement for reading it:

- Save the `.drawio` source alongside any rendered PNG/SVG, inside the repo or project folder it documents, so it stays editable.
- Single wide page (2400+ px), a header strip of three to five boxes, stage containers left to right, a data store column on the right, footer notes.
- Reuse the palette for groupings and containers even when using built-in shape libraries (`aws4`, `mscae`, `azure`, `gcp`, `cisco`) for domain icons.
- When a diagram in the same family already exists, copy it and rewrite the contents rather than starting from scratch.

## 2. Write the XML

Author the `.drawio` file directly. Manage cell IDs carefully (every `mxCell` needs a unique id, and edges reference `source` and `target` ids), escape `&`, `<`, and `>` in labels, and lay out coordinates by hand following the grammar. Layout is a judgment call, not something to automate.

## 3. Validate and render (this is the point of the skill)

Never report a diagram done without rendering it and looking at the image. Run:

```
python3 ~/.claude/skills/draw-diagram/render.py <path/to/file.drawio>
```

This validates the XML is well-formed and a real draw.io document (printing page, node, and edge counts), then exports a PNG next to the source.

Three advisory checks run with the validation. None of them block, and none replaces looking at the image:

- **Partial box overlaps**, per page, in absolute coordinates. Containment is fine, since a stage container holds its boxes. Partial overlap is the bug, because it renders as text printed over text. Icons deliberately laid over a node are skipped.
- **Text cells that cannot wrap**, where the label looks wider than the cell. A `text;html=1` cell without `whiteSpace=wrap` runs its prose past the cell edge while validation and export both succeed.
- **Compressed pages**, whose content the cell-level checks cannot read at all. Turn off Extras > Compressed in the draw.io app and re-save.

Options:

- `--format svg` (or `pdf`) for a different output
- `-o <path>` for an explicit output location
- `--validate-only` to skip the export (well-formedness and counts only)
- `--scale` and `--border` to tune the raster

Then **read the produced PNG/SVG** to confirm the diagram renders as intended: no overlapping nodes, readable labels, edges connecting the right boxes, palette applied consistently. Fix the XML and re-render until it is right. Do not claim it is done blind, the same way you would not claim a UI change works without loading the page.

### Renderer dependency

The export step needs the draw.io desktop engine. `render.py` finds it automatically on `PATH` or in the usual install locations on macOS, Linux, and Windows. If it is missing, the script still validates and prints the install command for your platform.

Validation always works with no dependencies (pure Python standard library), so a missing renderer never blocks producing a valid, editable `.drawio` file. It only blocks the visual check.

On a headless Linux box the draw.io desktop binary needs a virtual display. Run the exporter under `xvfb-run` if it fails there.

## 4. Report

State where you saved the `.drawio` source and the rendered image, and confirm you visually verified the render.
