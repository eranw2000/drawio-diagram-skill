# draw.io diagram grammar

The visual grammar the `draw-diagram` skill follows: shape vocabulary, color palette, layout skeleton, arrow conventions, and the layout traps that only show up once you look at the rendered image.

Read this before authoring a `.drawio` file. The point of a shared grammar is that a reader who has seen one of your diagrams can read the next one without re-orienting, so keep the vocabulary stable across projects and adapt only what the domain forces you to change.

## When to use draw.io

Whenever a diagram is needed, produce it as a draw.io file. Two acceptable outputs:

- `.drawio` (raw XML). Preferred when you will keep editing it.
- `.drawio.png` or `.drawio.svg` (PNG/SVG with the XML embedded). Useful for README embeds, and the file stays editable in draw.io.

If a project already has Mermaid diagrams in the README and you are asked to extend them, follow the existing convention rather than mixing tools.

Save the source file alongside the rendered PNG/SVG, inside the repo or project folder it documents, so it can be re-opened and edited later. A rendered image with no source is a dead end.

When you already have a diagram in the same family, copy it and rewrite the contents rather than reinventing shape semantics and layout structure from scratch.

## Layout

Single page, wide canvas (2400+ px). A header strip of three to five side-by-side boxes, then stage containers laid out left to right and top to bottom, then a data store column on the right, then a footer with operational notes.

Header boxes worth having: Legend, Key IDs, Actors, a domain summary (Forms, Services, Endpoints, Environments), and an operational box (versions, last deploy, monitoring URL).

For projects that are not workflow automations, swap "stage" for whatever the natural left-to-right grouping is: regions, layers, lifecycle phases, request stages. Keep the header, body, data column, footer skeleton, which scales to any system.

## Shape vocabulary

Keep colors consistent across projects so every diagram reads the same way.

- Actor, trigger, inbox, external user: `ellipse` `fillColor=#d5e8d4 strokeColor=#82b366`
- Process, action, function, API call: `rounded=1` `fillColor=#dae8fc strokeColor=#6c8ebf`
- Loop (for-each, batch): `rounded=1` `fillColor=#bbdefb strokeColor=#1976d2 fontStyle=1`
- Decision, branch, switch case: `rhombus` `fillColor=#f8cecc strokeColor=#b85450`
- Form, document, user input artifact: `shape=document` `fillColor=#ffe6cc strokeColor=#d79b00`
- Data store (DB table, spreadsheet, bucket, queue): `shape=cylinder3 size=15` `fillColor=#fff2cc strokeColor=#d6b656`
- Email, notification, outbound message: `rounded=1` `fillColor=#e1d5e7 strokeColor=#9673a6`

For domain-specific icons (a Lambda mark on an AWS diagram, a framework logo on a frontend component), use draw.io's built-in shape libraries (`mscae`, `aws4`, `gcp`, `azure`, `cisco`), but keep the palette above for groupings and containers so the page stays coherent.

### Real brand logos

draw.io does not ship most third-party brand logos. To put real brand marks in a diagram, embed them as images from [Simple Icons](https://simpleicons.org), a per-brand monochrome SVG set made for this:

1. Fetch each at `https://cdn.jsdelivr.net/npm/simple-icons@<major>/icons/<slug>.svg`.
2. Recolor it by injecting `fill="#HEX"` on the `<svg>` or `<path>`, using the brand color, or a dark neutral like `#2b2f3a` when the brand color is too light on a white bubble.
3. Base64-encode it and overlay it as a centered `shape=image` cell on top of the node, keeping a white ellipse behind it for the ring.

Fetch during generation and bake the base64 strings into the generator or a sidecar file, so it can re-run offline. For a brand with no clean icon, or an internal node that has no brand at all, fall back to an emoji glyph rather than force a wrong logo.

**Renderer gotcha that costs real time:** the draw.io CLI exporter does not render `image=data:image/...;base64,...`, because the mxGraph style parser splits on `;` and the `;base64` token truncates the image. You get a broken-image placeholder, and both SVG and PNG data URIs fail the same way. The working form is the drawio-native `image=data:image/svg+xml,<base64>`, with a comma and no `;base64`, plus an explicit `width` and `height` on the SVG root.

### Text cells silently do not wrap without `whiteSpace=wrap`

A plain `text;html=1` cell does not wrap. Long prose runs straight past the cell's width, over whatever sits beside it, and nothing reports it: XML validation only checks well-formedness, so the export succeeds while the page is unreadable.

Shape cells (`rounded=1`, `rhombus`, `shape=document`, containers) are usually authored with `whiteSpace=wrap` already, so this lands specifically on the plain text cells used for header body copy, footers, and notes inside a box.

Two things make it easy to miss. Explicit `<br>` tags mask it, so a hand-broken Courier block renders fine right next to a paragraph that is overflowing. And the overflow reads as "the neighbouring box has messy text" rather than as a bug in the cell you wrote.

Write `text;html=1;whiteSpace=wrap;` every time. `render.py` warns when an unwrappable text cell's label looks wider than its cell, which catches the cause, but the estimate is font-metric-free, so looking at the PNG is still the real check.

### A generated diagram protects its coordinates, not its claims

Generating a `.drawio` from a script keeps the layout from going stale when things are renumbered. It does nothing for the sentences, which sit hand-written inside the generator and drift like any other doc.

A measured example: a generated flow diagram said "three of them do real work" while its own status box listed four, and labelled two services as fully controlled when the codebase had no client for either. Both statements had been true once.

So any count or status a diagram asserts about code should be derived from that code at build time (import the registry, count the real handlers) rather than typed into the generator. Anything that cannot be derived gets re-checked against the code whenever the diagram is regenerated. Treat the generator's prose as documentation, not as layout.

### Diagram prose escapes your normal text checks

Whatever standards you apply to your writing apply to the words inside a diagram, and diagram text drifts from them by default, because it does not pass through the tools that check everything else. An editor hook that fires on file writes never sees a `.drawio` rewritten by a script through a shell heredoc, which is the natural way to do bulk cell edits.

Scan the prose explicitly before reporting a diagram done. Pull every `value="..."` attribute, unescape twice (the entities are double-encoded), strip the inline HTML, then read the result:

```python
import re, html, pathlib
s = pathlib.Path("file.drawio").read_text()
prose = []
for v in re.findall(r'value="([^"]*)"', s):
    t = html.unescape(html.unescape(v))       # entities are DOUBLE-encoded
    t = re.sub(r'<[^>]+>', ' ', t)            # strip the inline HTML
    prose.append(re.sub(r'\s+', ' ', t).strip())
print("\n".join(p for p in prose if p))
```

## Stage containers

`rounded=0 fillColor=#fafafa strokeColor=#0050b3 fontStyle=1 verticalAlign=top align=left spacingLeft=10 spacingTop=6 dashed=1 strokeWidth=2`

Stroke color carries meaning:

- Blue (`#0050b3`): flow and process stages.
- Purple (`#9673a6`): user-facing stages (forms, reviewer steps).
- Gray (`#999999`): setup, config, one-off admin stages.

For other domains, pick a two or three color stroke scheme that fits (green for production, yellow for staging, red for sensitive zones in an infrastructure diagram) and document it in the Legend header box.

## Arrows

`edgeStyle=orthogonalEdgeStyle rounded=0 endArrow=classic`

Cross-stage thick arrows:

- `strokeColor=#0050b3 strokeWidth=2`: data writes. Add `dashed=1` for reads.
- `strokeColor=#82b366 strokeWidth=2`: actor handoffs (form submit to trigger, user click to API call).
- `strokeColor=#9673a6 strokeWidth=2`: emails and notifications into inbox boxes.
- `strokeColor=#999999 dashed=1`: read-only lookups. Often with `startArrow=classic` to mark a bidirectional reference rather than a write.

The same convention carries to architecture diagrams: writes blue, reads gray dashed, async events purple, sync user actions green.

## Cross-stage data store column

A wide dashed-border band on the right (`fillColor=#fffbe6 strokeColor=#d6b656 strokeWidth=2`) holding every data store plus auxiliary blocks: named ranges, deploy and verify tooling, status transition graphs, inboxes, connection references, deploy state. Auxiliary blocks are styled as `text;html=1` with a `fillColor` matching the relevant shape category, so the band reads as a vertical legend of system facts.

For other domains the same band works as shared infrastructure: databases, caches, queues, brokers, secret stores, observability sinks. Group by category and color-match to the producer and consumer shapes elsewhere on the canvas.

## Header boxes

`rounded=0 fillColor=#f5f5f5 strokeColor=#666666 fontStyle=1 verticalAlign=top align=left spacingLeft=10 spacingTop=6`

The Key IDs box uses `fontFamily=Courier New` for IDs, GUIDs, ARNs, and connection strings in a tabular layout. Actors and prose boxes use `Helvetica` for readability.

Never put a live secret in a Key IDs box. Diagrams get shared, exported to PNG, and pasted into documents far more casually than code does.

## Temporal ordering of switch cases and decision hubs

Read this before drawing any decision-hub flow.

A switch action, or any other fan of parallel children, renders as parallel. In production a single switch can fire many times across many invocations as upstream state advances, and each invocation matches one case. A naive parallel layout makes case N look like it runs at the same time as case N+1, which is wrong when cases depend on form responses, manual status changes, or external events that happen in between.

A worked example. A recruitment workflow had a six-case switch. Case 1 fires once an operator advances a candidate's status. A form then collects the candidate's scheduling preferences. The operator reads those preferences and sets a second status. Only then does Case 3 fire. The original diagram drew all six cases in parallel, which hid that Case 3 sits downstream of Case 1, plus a form submission, plus a manual step by a human.

Three defenses. Apply at least the first to any decision-hub diagram.

1. **Temporal markers in case labels.** Prefix each case with `[T1, EARLY]`, `[T2, MID, fires AFTER stage X]`, `[T3, LATE]`. Reject branches get `[REJECT-EARLY]` and `[REJECT-MID]`. Readers see the time axis immediately.

2. **Feedback-loop arrows.** When a downstream stage writes data that the next case depends on, draw a thick dashed colored arrow (`strokeColor=#b85450 strokeWidth=3 dashed=1 curved=1`) from the downstream data cell back to the cell where the operator sets status, labelled with what the operator reads before acting. This makes the loop topology visible.

3. **Step-by-step timeline panel.** Replace or complement the parallel case fan with a numbered table walking through the actual operator and system events in order: step 1 operator sets status A to B, step 2 the T1 case fires and sends a form, step 3 the candidate submits and the table updates, step 4 the operator reads the column and sets status B to C. Cleanest for a hub-and-spoke detail page.

The lesson applies to any decision tree, state machine, or event-driven architecture where branches look parallel but actually fire across time as upstream state advances.
