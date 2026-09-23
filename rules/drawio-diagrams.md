# draw.io is the default tool for every diagram

Whenever a diagram is needed (architecture, data flow, sequence, decision tree, system map, ER diagram, org chart, network topology, state machine, infrastructure layout, anything visual), produce it as a **draw.io** file with the `draw-diagram` skill. Do not default to Mermaid, ASCII art or Graphviz unless the user asks for one of them.

The skill pairs the visual grammar in `DRAWIO_DIAGRAMS.md` with `render.py`, which validates the file and exports it to PNG.

**Always render the diagram and look at the image before calling it done.** A file can validate clean and still render wrong: a label that vanished, an arrow that starts in empty space, a legend clipped by a container border. Only looking at the picture catches those.
