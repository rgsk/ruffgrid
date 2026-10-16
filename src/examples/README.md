# Examples

Each file shows what ruffgrid changes in Ruff, written in its "before save" state. Open one in VS Code with the ruffgrid extension, save it, and run `git diff` to see what changed. `git checkout src/examples` puts the files back.

| File | Shows |
|---|---|
| `grids.py` | Grids: a bracket whose first line holds several items stays packed to that line's width. It also covers hugging the bracket, nesting, width rather than count, and the two ways back to Ruff's layout (one item on the first line, or no trailing comma). |
| `imports.py` | Fix all removes an unused import. Organize imports sorts the block and keeps a `from m import (` grid. |
| `notebook.ipynb` | The first cell is markdown, and organize imports and fix all still run on save. Ruff sees the whole notebook, so imports used only in the last cell stay. It also has grids in cells and a cell with a magic. |

The repo's `.vscode/settings.json` turns these on for save. Use the same settings in your own projects.
