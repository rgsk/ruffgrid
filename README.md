# ruffgrid

Ruff in VS Code for `.py` files and Jupyter notebooks, with two changes. It's a replacement for the Ruff extension: run one or the other in a workspace, not both.

- **Grids:** if the first line inside a split bracket holds several items, formatting packs the later lines up to its width instead of putting one item per line:

  ```python
  s = [
      1, 2, 3,
      4, 5, 6,
  ]
  bfs(
      n, m, k,
      start=(sr, sc),
      blocked=walls,
  )
  ```

  - **Hugging the bracket:** the first line can also start right after the bracket, as in `s = [1, 2, 3,`.
  - **Trailing comma:** as with Ruff's magic trailing comma, the last item needs one.
  - **Going back to Ruff's layout:** put a single item on the first line.
  - **Organize imports:** a `from m import (` grid keeps its packing. Ruff sorts the names, and they're packed again with the same number on the first line.
- **Notebook actions on save always run.** In the Ruff extension, `notebook.source.organizeImports` and `notebook.source.fixAll` do nothing when the notebook's first cell is markdown. Here they always run. Ruff sees the whole notebook, so an import used in a later cell isn't removed.

## How it works
VS Code talks to `ruffgrid`, and `ruffgrid` talks to `ruff server`.

- **Answered by `ruffgrid`, using the Ruff CLI:**
  - formatting a file or a cell
  - `source.fixAll` and `source.organizeImports` on a `.py` file
  - `notebook.format`, `notebook.source.fixAll` and `notebook.source.organizeImports`
- **Answered by `ruff server` as usual:** diagnostics, quick fixes, hover and range formatting. Range formatting doesn't keep grids.
- **Config:** both read the project's `[tool.ruff]` (or `ruff.toml`). The `ruff.*` VS Code settings go to `ruff server` as they do from the Ruff extension. For example, `"ruff.lint.enable": false` turns off Ruff's warnings in the editor; format and fixes on save still run.
- **Which `ruff` runs:** the project's `.venv/bin/ruff` if there is one, otherwise the copy installed with ruffgrid, otherwise `ruff` on PATH.

## Install
```sh
uv tool install /home/rahul/Documents/codes/projects/ruffgrid   # puts `ruffgrid` on PATH, for every project
cd editors/vscode && npm install && vsce package --allow-missing-repository --skip-license -o ruffgrid.vsix
code --install-extension ruffgrid.vsix
```

Disable the Ruff extension in the workspace, then add this to `.vscode/settings.json` (or your user settings):

```json
{
  "ruff.lint.enable": false,  // optional: no Ruff warnings in the editor
  "[python]": {
    "editor.defaultFormatter": "rgsk.ruffgrid",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": { "source.fixAll": "explicit", "source.organizeImports": "explicit" }
  },
  "notebook.formatOnSave.enabled": true,
  "notebook.codeActionsOnSave": {
    "notebook.source.fixAll": "explicit",
    "notebook.source.organizeImports": "explicit"
  }
}
```

The server is found as `<workspace>/.venv/bin/ruffgrid`, then `ruffgrid` on PATH. To use something else, set `ruffgrid.serverCommand`, for example `["uv", "run", "ruffgrid"]`.

## Development
`uv run pytest`. The tests drive the real Ruff.

The grid code (`grid`, `ungrid`, `regrid_imports`) is copied from byname's `fmt.py`. ruffgrid doesn't depend on byname.
