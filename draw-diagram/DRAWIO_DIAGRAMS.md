# draw.io diagram rules and grammar

The rules and the visual grammar the `draw-diagram` skill follows. The checklist comes first, one line per rule, each naming the section that holds the rule and the case that taught it. The grammar (shapes, colors, layout) follows the rules.

## Checklist

**Before you write the file**

- Use draw.io, not Mermaid, ASCII or Graphviz, and keep the source next to the picture. ([When to use draw.io](#when-to-use-drawio))
- On a page with more than about six stages: an arrow joins neighbours only and carries no label, a branch is two boxes side by side, a note takes no arrow. An edge that must carry a label gets a white label background; a corridor between columns stays free, with the edge's sides pinned. ([Arrows join neighbours](#arrows-join-neighbours-and-carry-no-label))
- Write every container before the boxes inside it. ([Containers first](#write-a-container-before-its-contents))
- A label is HTML inside an XML attribute: escape it twice, through one `attr()` helper at the point a cell is written. Never align with runs of spaces, and derive a header's width from the content's. ([Labels are escaped twice](#labels-are-escaped-twice))
- `whiteSpace=wrap` on every text cell. ([Text cells do not wrap](#text-cells-do-not-wrap-without-whitespacewrap))
- Compute box heights from the text, with extra room for cylinders and documents, and stack each column from a running total. Anchor the footer below every column, and each side panel beside the stage it talks about. ([Stack columns in code](#stack-columns-in-code-never-hand-place-coordinates); [Cylinders and documents](#cylinders-and-documents-need-more-height-than-a-rectangle))
- A Hebrew page: `dir="rtl"` on every label, stage A on the right, a control's column assigned by hand, and `path()` for a mixed Latin and Hebrew path. ([Hebrew](#hebrew-right-to-left-labels-and-a-right-to-left-page))
- A decision hub: a temporal marker on every case. ([Temporal ordering](#temporal-ordering-of-switch-cases-and-decision-hubs))
- Any count or status the diagram states about code is derived from that code; whatever cannot be derived is re-checked against the code on every rebuild. ([Generated diagrams](#a-generated-diagram-protects-its-coordinates-not-its-claims))
- A brand logo comes from Simple Icons as `image=data:image/svg+xml,<base64>`, baked into the generator. ([Real brand logos](#real-brand-logos))
- No live secret in a Key IDs box. ([Header boxes](#header-boxes))

**Before you report it done**

- Zero WARNING lines from `render.py`, or name the deliberate one. ([Zero WARNING lines](#zero-warning-lines-or-name-the-deliberate-one))
- Read every crop `render.py` prints, then the page, and check its LOOK FOR line: edges through boxes, labels on nodes, stacked arrowheads, Hebrew reading order, the bottom edge of every picture. ([Fixed-size panels](#a-fixed-size-panel-overflows-silently-when-its-text-grows); [Arrows join neighbours](#arrows-join-neighbours-and-carry-no-label))
- Read the label text the way you read any other prose you write. ([Diagram prose](#diagram-prose-is-prose))

## When to use draw.io

Whenever a diagram is needed, produce it as a draw.io file:

- `.drawio` (raw XML). Preferred when the diagram will keep being edited.
- `.drawio.png` or `.drawio.svg` (PNG/SVG with the XML embedded). Useful for README embeds; the file stays editable in draw.io.

Do not default to Mermaid, ASCII art, Graphviz or PlantUML. If a project already has Mermaid diagrams and you are asked to extend them, follow the existing convention rather than mixing tools.

Save the source file beside the rendered PNG/SVG, inside the repo or project folder it documents, so it can be re-opened and edited later. A rendered image with no source is a dead end.

When you already have a diagram in the same family, copy it and rewrite the contents rather than reinventing shape semantics and layout.

## Rules, each with the case that taught it

### Arrows join neighbours, and carry no label

Reach for these first on any page with more than about six stages.

- **An arrow joins neighbours only:** two boxes stacked next to each other, or two stage containers side by side with the arrow crossing the gap. Where the flow really jumps, say so in the target box's own text ("the pass is issued next, in B"), an off-page connector by another name.
- **No arrow carries a label.** The gap between stacked boxes is thinner than a line of text, so a midpoint label lands on the box below. Put the branch word in bold at the START of the target box: "No. Nothing is issued."
- **A branch is two boxes SIDE BY SIDE**, half width on one row, so both arrows are short and parallel. Stacked outcomes force the second arrow through the first.
- **A note takes no arrow.** Commentary is not a step; once notes leave the arrow graph, the remaining arrows are few enough to place by hand and check by eye.
- Where an edge does need a label, give it `labelBackgroundColor=#ffffff` (a collision then shows as a white patch instead of a node that seems to gain a line), and slide it along the edge with `<mxGeometry x="<-1..1>" relative="1">` plus an `<mxPoint as="offset"/>` rather than rerouting.
- Where a connector passes between columns, keep that corridor free and pin the sides (`exitX`/`exitY`/`entryX`/`entryY`), so a later edit cannot let draw.io re-pick a side and re-enter a box.

Edge labels and connector lines are judged by eye, so these are layout rules, and the look after every render names them.

### Write a container before its contents

draw.io paints cells in document order, so a stage container or band written after the boxes it holds covers all of them. The page validates, the node count is right (the children exist, underneath), and the render is an empty colored rectangle. `render.py` warns on it ("hidden under a filled box written after them"), judging paint order by the parent tree: a container that is the PARENT of its boxes is fine wherever it sits, and an unfilled shape never counts.

### Labels are escaped twice

A label is HTML inside an XML attribute. A raw `value="<b>Title</b>"` is not well-formed XML, and `render.py` refuses the file with `INVALID: not well-formed (invalid token)`, naming a line rather than a cell, which is slow to trace in a generated file. Build the label as real HTML with its own text HTML-escaped, then XML-escape the whole string, in one helper applied where the cell is written:

```python
from xml.sax.saxutils import escape

def attr(text):                      # text already contains real <b>, <br>
    return escape(text, {'"': "&quot;"})
```

Two quiet failures from the same cause:

- **Runs of spaces collapse**, because the label is HTML. A hand-aligned column table renders as ragged prose even in `Courier New`. Use an explicit separator, or a bold name then its description.
- **A hardcoded header width stretches the canvas.** A header row wider than the content column runs past its right edge, and the export grows to fit. Derive the header width from the content width.

### Stack columns in code; never hand-place coordinates

Authored `y` values encode the text lengths of the moment. Add a sentence and the next box overlaps, or the last overflows its band, while every box still validates. Compute positions from a list of (id, label, height) and size the container from the running total; past about ten boxes, write a generator.

- Anchor a footer below ALL columns, not the one being edited: a footer placed from one column can overlap the other.
- Place a side panel at `max(running_y, stage_top[anchor])`, with `anchor` the stage it talks about. The running total alone bunches panels at the top of a tall page; the anchor alone lets a near anchor overlap the panel above.

### Text cells do not wrap without `whiteSpace=wrap`

A plain `text;html=1` cell does not wrap. Long prose runs past the cell, over its neighbour, and the export still succeeds. Shape cells usually carry `whiteSpace=wrap` already, so this lands on plain text cells: header body copy, footers, notes inside a box. Explicit `<br>` tags mask it, and the overflow reads as messy text in the NEIGHBOUR. Write `text;html=1;whiteSpace=wrap;` every time.

`render.py` warns on partial box overlaps (containment is normal) and estimates text overflow (`check_text_wrap` sideways, `check_text_height` downwards) from a character width; read the crops to confirm.

### A fixed-size panel overflows silently when its text grows

A text cell does not grow with its content. Lengthen a status panel by a paragraph and the last line renders over the border, while every content assertion passes, because the text is present, just outside its box. Two fixes, the first usually right:

- **Tighten the wording** back toward the original length, when the panel sits in a grid (growing it pushes every zone below).
- **Grow the box AND its parent**, then re-render and check what moved.

On a page wider or taller than 2000 px, `render.py` writes one full-size crop per stage container into `<page>.crops/` (`CROP:` lines); read the panel's crop first. To crop one cell by hand: the export starts at the leftmost and topmost thing drawn (`xmin`, `ymin`, edge waypoints included, not the end point of an end joined to a box), so a cell at `x,y,w,h` lands at `left = border*scale + (x - xmin)*scale`, `top = border*scale + (y - ymin)*scale`; crop to `+ w*scale`, `+ h*scale` with a generous margin on the right and bottom (`crop_regions` in `render.py` does this). A line touching or crossing the border is the failure.

### Cylinders and documents need more height than a rectangle

A height estimate (`est_height` style) sizes a RECTANGLE. A `shape=cylinder3` spends its caps on geometry and a `shape=document` its wavy foot, so the same height puts the last line across the bottom border. Add about 46 px for a cylinder and 28 px for a document where the height is computed, and a small padding bump on every box (the shared estimate can run a line short on a wrapped rounded box). `render.py`'s height check takes the same 46 and 28 px off before comparing, and names the shape.

### Hebrew: right-to-left labels and a right-to-left page

- **Every label gets `dir="rtl"`.** Its base direction is LTR otherwise, and the bidi algorithm puts a trailing Latin run at the visual left: `דוח חודשי.xlsx` renders as `xlsx.דוח חודשי`. Every file name ends in `.xlsx`, `.csv` or `.py`, so it lands on the lines carrying the facts. `align=right` does not fix it; alignment places the block, direction orders the runs. One wrapper where cells are written:

  ```python
  def rtl(label):
      return '<div dir="rtl" style="text-align:right">' + label + '</div>'
  ```

- **The page reads right to left too.** Stage A is the RIGHTMOST band and column 0 the rightmost column: `x0 + (n - 1 - i) * (w + gap)`, not `x0 + i * (w + gap)`, in the header strip and inside every stage, with the arrows running right to left.
- **A control's column is a layout decision, not an index.** An `i % cols` grid put a control beside the step AFTER the one it checks. Tag each box with its column.
- **A mixed Latin and Hebrew PATH needs the opposite wrapper.** `rtl()` reorders the Hebrew placeholders inside `output/<חברה>/runs/<מזהה הרצה>/`, so the label names a path that does not exist. Pin the run left to right, and write placeholders in Latin (`<company>`, `<run-id>`):

  ```python
  def path(t):   # t carries &lt; and &gt; for the literal angle brackets
      return ('<span dir="ltr" style="font-family:Courier New;unicode-bidi:embed">' + t + '</span>')
  ```

`render.py` warns on a Hebrew label with no direction whose line starts or ends with a Latin run. Check the page's column order in the render.

### A generated diagram protects its coordinates, not its claims

Generating a `.drawio` from a script keeps the layout from going stale. It does nothing for the sentences typed into the generator, which drift like any other doc. So any count or status a diagram asserts about code is derived from that code at build time (import the registry, count the real handlers), and whatever cannot be derived is re-checked against the code whenever the diagram is regenerated. Treat the generator's prose as documentation, not as layout.

The shape to watch for: a status box listing four handlers beside a sentence that says three.

### Diagram prose is prose

The standards you apply to your writing apply to the words in a diagram, and diagram text drifts from them, because a `.drawio` rewritten by a script through a shell heredoc never passes the checks that fire on file writes. `render.py` scans every label on every run (`check_label_prose`): it decodes the double-encoded entities, strips the inline HTML, and warns per cell on em dashes, curly quotes and box-drawing characters, naming the cell and the problem, never the text. A page whose labels decode to no text at all is reported, not passed.

### Zero WARNING lines, or name the deliberate one

Every check in `render.py` is advisory: it prints WARNING lines and never fails the export. A warning left unread is how a check earns being ignored, so before reporting a diagram done, either clear every WARNING line or name the one that is deliberate and why (a shared trunk drawn on purpose, say).

### Temporal ordering of switch cases and decision hubs

A `Switch` (any fan of parallel children) renders as parallel, but in production one switch fires many times as upstream state advances, each run matching one case. A parallel layout makes case N look simultaneous with case N+1, which is wrong when cases depend on form responses, manual status changes, or events in between. Apply at least the first defense to any decision hub:

1. **Temporal markers in case labels:** `[T1, EARLY]`, `[T2, MID, fires AFTER stage X]`, `[T3, LATE]`; reject branches `[REJECT-EARLY]`, `[REJECT-MID]`.
2. **Feedback-loop arrows:** when a downstream stage writes data the next case depends on, a thick dashed arrow (`strokeColor=#b85450 strokeWidth=3 dashed=1 curved=1`) from that data cell back to where the operator sets status, labelled with what the operator reads.
3. **A step-by-step timeline panel:** a numbered table of operator and system events in order (step 1 operator sets status A to B, step 2 the T1 case fires and sends a form, step 3 the form updates the table, step 4 the operator reads it and sets B to C).

The same holds for any decision tree, state machine or event-driven design whose branches look parallel but fire across time.

Example: a switch whose Case 3 fires only after Case 1, a form response and a manual status change looks simultaneous when the cases are drawn in parallel.

### Real brand logos

draw.io ships few third-party brand logos. Embed them from [Simple Icons](https://simpleicons.org): fetch `https://cdn.jsdelivr.net/npm/simple-icons@<major>/icons/<slug>.svg`, recolor it (`fill="#HEX"` on the `<svg>` or `<path>`: the brand color, or `#2b2f3a` when that is too light on a white bubble), base64-encode it, and overlay it as a centered `shape=image` cell with a white ellipse behind it. Bake the base64 into the generator so it re-runs offline; for a brand with no clean icon, use an emoji glyph rather than a wrong logo.

**Exporter gotcha:** the draw.io CLI does not render `image=data:image/...;base64,...`: the style parser splits on `;`, so the image is cut and you get a broken-image placeholder (SVG and PNG alike). Use the drawio-native `image=data:image/svg+xml,<base64>` (a comma, no `;base64`) with an explicit `width` and `height` on the SVG root.

## Grammar

### Layout

Single page, wide canvas (2400+ px). A header strip of three to five side-by-side boxes (Legend, Key IDs, Actors, plus a domain summary such as Forms, Services, Endpoints or Environments), then stage containers A to G (or A to M) left to right and top to bottom, then a data-store column on the right, then a footer with operational notes. Outside workflow automation, swap "stage" for the natural grouping (regions, layers, lifecycle phases, request stages) and keep the skeleton.

When you already have a diagram in the same family, it is the best starting point.

### Shape vocabulary

Keep the colors the same across projects, so every diagram reads the same way.

- Actor, trigger, inbox, external user: `ellipse` `fillColor=#d5e8d4 strokeColor=#82b366`
- Process, action, function, API call: `rounded=1` `fillColor=#dae8fc strokeColor=#6c8ebf`
- Loop (for-each, batch): `rounded=1` `fillColor=#bbdefb strokeColor=#1976d2 fontStyle=1`
- Decision, branch, switch case: `rhombus` `fillColor=#f8cecc strokeColor=#b85450`
- Form, document, user input: `shape=document` `fillColor=#ffe6cc strokeColor=#d79b00`
- Data store (list, table, DB, bucket, queue): `shape=cylinder3 size=15` `fillColor=#fff2cc strokeColor=#d6b656`
- Email, notification, outbound message: `rounded=1` `fillColor=#e1d5e7 strokeColor=#9673a6`

For domain icons use draw.io's libraries (`mscae`, `aws4`, `gcp`, `azure`, `cisco`), and keep this palette for groupings and containers.

### Stage containers

`rounded=0 fillColor=#fafafa strokeColor=#0050b3 fontStyle=1 verticalAlign=top align=left spacingLeft=10 spacingTop=6 dashed=1 strokeWidth=2`

The stroke color carries meaning: blue (`#0050b3`) flow and process stages, purple (`#9673a6`) user-facing stages (forms, reviewers), gray (`#999999`) setup, config and one-off admin. In other domains pick a two or three color scheme (green production, yellow staging, red sensitive zones) and document it in the Legend.

### Arrow colors

`edgeStyle=orthogonalEdgeStyle rounded=0 endArrow=classic`, and for cross-stage thick arrows:

- `strokeColor=#0050b3 strokeWidth=2`: data writes (`dashed=1` for reads).
- `strokeColor=#82b366 strokeWidth=2`: actor handoffs (form submit to trigger, click to API call).
- `strokeColor=#9673a6 strokeWidth=2`: emails and notifications into inbox boxes.
- `strokeColor=#999999 dashed=1`: read-only lookups, often with `startArrow=classic` for a two-way reference.

In architecture diagrams: writes blue, reads gray dashed, async events purple, sync user actions green.

### Cross-stage data-store column

A wide dashed band on the right (`fillColor=#fffbe6 strokeColor=#d6b656 strokeWidth=2`) holding every data store, drawn as its shape from the vocabulary (a cylinder for a table or list), plus auxiliary blocks (named ranges, deploy and verify tooling, status transitions, inboxes, connection references, deploy state). Only the auxiliary blocks are `text;html=1` blocks, each with a `fillColor` matching the relevant shape category, so the band reads as a legend of system facts. In other domains it holds the shared infrastructure: databases, caches, queues, brokers, secret stores, observability sinks. Group them by category and color-match them to the producer and consumer shapes elsewhere on the canvas.

### Header boxes

`rounded=0 fillColor=#f5f5f5 strokeColor=#666666 fontStyle=1 verticalAlign=top align=left spacingLeft=10 spacingTop=6`

Recommended: **Legend** (shapes and arrow colors), **Key IDs** (environment IDs, ARNs, tenant IDs, connection references), **Actors** (who interacts and how), a **domain summary** (data stores, services, endpoints), and **Deploy / Status** (versions, last deploy, monitoring URL).

The Key IDs box uses `fontFamily=Courier New` for IDs, GUIDs, ARNs and connection strings in a tabular layout; Actors and every other prose box use `Helvetica`.

Never put a live secret in a Key IDs box: diagrams are shared, exported to PNG and pasted into documents far more casually than code.
