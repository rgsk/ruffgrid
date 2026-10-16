"""ruffgrid: Ruff's language server for .py files and notebooks, with grids (see grid.py).

    editor <--LSP--> ruffgrid <--LSP--> ruff server

Everything goes to `ruff server` (diagnostics, quick fixes, hover) except what grids change, which is
answered here with the Ruff CLI on the text the editor has open:
- formatting, of a .py file or a notebook cell;
- source.fixAll / source.organizeImports on a .py file (codeActionsOnSave);
- the whole-notebook actions on save: notebook.format, notebook.source.fixAll / organizeImports.

VS Code asks for the notebook actions once, on the notebook's first cell, which may be markdown and so
outside a Python server's selector (the Ruff extension's organize imports does nothing then). The
VS Code extension offers them on every cell and asks here on the first code cell; the edit covers
every code cell, and Ruff sees the whole notebook: an import used in a later cell is used.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from .grid import ORGANIZE, FormatError, UnparsableError, fix_cells, fix_py, format_cells, format_py, resolve_ruff

FIX_ALL, ORGANIZE_IMPORTS = "source.fixAll", "source.organizeImports"
NOTEBOOK_FORMAT, NOTEBOOK_FIX_ALL, NOTEBOOK_ORGANIZE = (
    "notebook.format",
    "notebook.source.fixAll",
    "notebook.source.organizeImports",
)


# --- JSON-RPC framing ---------------------------------------------------------


def read_message(stream) -> dict | None:
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        key, _, value = line.decode("ascii").partition(":")
        if key.lower() == "content-length":
            length = int(value)
    if length is None:
        return None
    return json.loads(stream.read(length))


class Writer:
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()

    def send(self, msg: dict) -> None:
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        with self.lock:
            self.stream.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self.stream.flush()


# --- helpers -----------------------------------------------------------------


class LineIndex:
    """Offsets <-> LSP positions (UTF-16 columns)."""

    def __init__(self, text: str):
        self.text = text
        self.starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self.starts.append(i + 1)

    def offset(self, line: int, col16: int) -> int:
        if line >= len(self.starts):
            return len(self.text)
        start = self.starts[line]
        end = self.starts[line + 1] - 1 if line + 1 < len(self.starts) else len(self.text)
        units = 0
        for i in range(start, end):
            if units >= col16:
                return i
            units += 2 if ord(self.text[i]) > 0xFFFF else 1
        return end

    def end(self) -> dict:
        last = self.starts[-1]
        return {"line": len(self.starts) - 1, "character": sum(2 if ord(c) > 0xFFFF else 1 for c in self.text[last:])}


def apply_changes(text: str, changes: list[dict]) -> str:
    """Apply LSP content changes, ranged (incremental) or whole-text, in order."""
    for ch in changes:
        if "range" not in ch:
            text = ch["text"]
            continue
        lines = LineIndex(text)
        s, e = (lines.offset(ch["range"][k]["line"], ch["range"][k]["character"]) for k in ("start", "end"))
        text = text[:s] + ch["text"] + text[e:]
    return text


def uri_to_path(uri: str) -> Path:
    u = urlparse(uri)
    if u.scheme not in ("file", "vscode-notebook-cell"):  # untitled: Ruff still wants a name, for config
        return Path.cwd() / "untitled.py"
    return Path(url2pathname(u.path))


def asked(params: dict, *kinds: str) -> str | None:
    """The one of kinds this codeAction request asks for alone (`only`, as codeActionsOnSave sends it:
    `source.fixAll`, or a subkind like `source.fixAll.ruff`), else None: Ruff answers it."""
    only = params.get("context", {}).get("only") or []
    for kind in kinds:
        if only and all(k == kind or k.startswith(kind + ".") for k in only):
            return kind
    return None


def whole(uri: str, old: str, new: str) -> dict:
    return {uri: [{"range": {"start": {"line": 0, "character": 0}, "end": LineIndex(old).end()}, "newText": new}]}


# --- the proxy ---------------------------------------------------------------


class Proxy:
    def __init__(self, ruff: str):
        self.ruff = ruff
        self.client = Writer(sys.stdout.buffer)
        self.proc = subprocess.Popen([ruff, "server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.server = Writer(self.proc.stdin)
        self.text: dict[str, str] = {}  # open .py files' and code cells' text, by uri
        self.notebooks: dict[str, list[str]] = {}  # notebook uri -> its cells' uris, in order (markdown too)
        self.cell_nb: dict[str, str] = {}  # code cell uri -> its notebook's uri
        self.initialize: object = None  # the initialize request's id

    def reply(self, mid, result) -> None:
        self.client.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def on_client(self, msg: dict) -> None:
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            caps = params.get("capabilities", {})
            caps.get("general", {}).pop("positionEncodings", None)  # we count columns in UTF-16
            self.initialize = mid
        elif method and method.startswith("notebookDocument/"):
            self.on_notebook(method, params)
        elif method == "textDocument/didOpen":
            self.text[params["textDocument"]["uri"]] = params["textDocument"]["text"]
        elif method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            self.text[uri] = apply_changes(self.text.get(uri, ""), params["contentChanges"])
        elif method == "textDocument/didClose":
            self.text.pop(params["textDocument"]["uri"], None)
        elif method == "textDocument/formatting" and (uri := params["textDocument"]["uri"]) in self.text:
            self.reply(mid, self.format(uri))
            return
        elif method == "textDocument/codeAction" and (uri := params["textDocument"]["uri"]) in self.text:
            if (kind := asked(params, NOTEBOOK_FORMAT, NOTEBOOK_FIX_ALL, NOTEBOOK_ORGANIZE)) and uri in self.cell_nb:
                self.reply(mid, self.notebook_action(self.cell_nb[uri], kind))
                return
            if kind := asked(params, FIX_ALL, ORGANIZE_IMPORTS):
                # a cell alone can't say which imports are unused (later cells may use them): the
                # notebook-wide actions do it
                self.reply(mid, [] if uri in self.cell_nb else self.fix(uri, kind))
                return
        self.server.send(msg)

    def on_notebook(self, method: str, params: dict) -> None:
        nb = params.get("notebookDocument") or {}
        uri = nb.get("uri")
        if method == "notebookDocument/didOpen":
            self.notebooks[uri] = [c["document"] for c in nb.get("cells", [])]
            for td in params.get("cellTextDocuments", []):
                self.text[td["uri"]], self.cell_nb[td["uri"]] = td["text"], uri
        elif method == "notebookDocument/didClose":
            for u in self.notebooks.pop(uri, []):
                self.text.pop(u, None)
                self.cell_nb.pop(u, None)
        elif method == "notebookDocument/didChange" and uri in self.notebooks:
            cells = (params.get("change") or {}).get("cells") or {}
            if st := cells.get("structure"):
                arr = st["array"]
                self.notebooks[uri][arr["start"] : arr["start"] + arr["deleteCount"]] = [
                    c["document"] for c in arr.get("cells") or []
                ]
                for td in st.get("didClose") or []:
                    self.text.pop(td["uri"], None)
                    self.cell_nb.pop(td["uri"], None)
                for td in st.get("didOpen") or []:
                    self.text[td["uri"]], self.cell_nb[td["uri"]] = td["text"], uri
            for tc in cells.get("textContent") or []:
                u = tc["document"]["uri"]
                self.text[u] = apply_changes(self.text.get(u, ""), tc["changes"])

    def code_cells(self, nb: str) -> list[str]:
        return [u for u in self.notebooks.get(nb, []) if u in self.text]

    def format(self, uri: str) -> list[dict]:
        """Whole-document format edits: Ruff with grids. Code that doesn't parse is left as it is."""
        old = self.text[uri]
        try:
            if uri in self.cell_nb:  # as a notebook cell: magics are fine
                new = format_cells([old], uri_to_path(self.cell_nb[uri]), self.ruff)[0]
            else:
                new = format_py(old, uri_to_path(uri), self.ruff)
        except UnparsableError:
            return []
        except FormatError as e:
            self.show(f"ruffgrid format: {e}")
            return []
        return [] if new == old else whole(uri, old, new)[uri]

    def fix(self, uri: str, kind: str) -> list[dict]:
        old = self.text[uri]
        try:
            new = fix_py(old, uri_to_path(uri), self.ruff, ORGANIZE if kind == ORGANIZE_IMPORTS else None)
        except FormatError:
            return []  # mid-edit code: offer nothing rather than fail the save
        if new == old:
            return []
        title = "Ruff: Organize imports" if kind == ORGANIZE_IMPORTS else "Ruff: Fix all auto-fixable problems"
        return [{"title": title, "kind": kind + ".ruff", "edit": {"changes": whole(uri, old, new)}}]

    def notebook_action(self, nb: str, kind: str) -> list[dict]:
        """A whole-notebook action, as one edit over its code cells."""
        code = self.code_cells(nb)
        old = [self.text[u] for u in code]
        path = uri_to_path(nb)
        try:
            if kind == NOTEBOOK_FORMAT:
                new = format_cells(old, path, self.ruff)
            else:
                new = fix_cells(old, path, self.ruff, ORGANIZE if kind == NOTEBOOK_ORGANIZE else None)
        except FormatError:
            new = old  # mid-edit code: offer nothing rather than fail the save
        changes = {}
        for u, o, n in zip(code, old, new):
            if n != o:
                changes |= whole(u, o, n)
        if not changes:
            return []
        title = {NOTEBOOK_FORMAT: "Format notebook", NOTEBOOK_FIX_ALL: "Fix all", NOTEBOOK_ORGANIZE: "Organize imports"}
        return [{"title": f"Ruff: {title[kind]}", "kind": kind + ".ruff", "edit": {"changes": changes}}]

    def show(self, message: str) -> None:
        self.client.send({"jsonrpc": "2.0", "method": "window/showMessage", "params": {"type": 1, "message": message}})

    def on_server(self, msg: dict) -> None:
        if msg.get("method") is None and msg.get("id") == self.initialize and "result" in msg:
            caps = msg["result"].setdefault("capabilities", {})
            cap = caps.get("codeActionProvider")
            if isinstance(cap, dict):  # not the notebook kinds: the extension offers them, on every cell
                cap["codeActionKinds"] = [k for k in cap.get("codeActionKinds", []) if not k.startswith("notebook.")]
            # Ruff's own format / fix commands (ruff.applyFormat, ...) would skip grids, and the editor
            # would register them as commands, clashing with the Ruff extension's
            caps.pop("executeCommandProvider", None)
            caps["documentFormattingProvider"] = True
        self.client.send(msg)

    def pump_server(self) -> None:
        while (msg := read_message(self.proc.stdout)) is not None:
            self.on_server(msg)
        os._exit(0)

    def run(self) -> None:
        threading.Thread(target=self.pump_server, daemon=True).start()
        stdin = sys.stdin.buffer
        while (msg := read_message(stdin)) is not None:
            self.on_client(msg)
        self.proc.terminate()


def main() -> None:
    """ruffgrid [RUFF]: serve LSP on stdio. RUFF: the ruff to run, else the project's (.venv in the
    current directory), else the one installed beside ruffgrid, else PATH's."""
    Proxy(sys.argv[1] if len(sys.argv) > 1 else resolve_ruff(Path.cwd())).run()
