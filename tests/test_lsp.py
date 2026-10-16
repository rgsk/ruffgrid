"""The server end to end: ruffgrid in front of the real `ruff server`, spoken to as VS Code would."""

import json
import subprocess
import sys
import threading
from pathlib import Path
from queue import Empty, Queue

import pytest

from ruffgrid.lsp import read_message


class Client:
    def __init__(self, root: Path):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from ruffgrid.lsp import main; main()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=root,
        )
        self.inbox: Queue = Queue()
        self.next_id = 0
        threading.Thread(target=self.pump, daemon=True).start()

    def pump(self):
        while (msg := read_message(self.proc.stdout)) is not None:
            self.inbox.put(msg)

    def send(self, msg: dict) -> None:
        body = json.dumps({"jsonrpc": "2.0", **msg}).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict) -> None:
        self.send({"method": method, "params": params})

    def request(self, method: str, params: dict):
        self.next_id += 1
        mid = self.next_id
        self.send({"id": mid, "method": method, "params": params})
        while True:
            try:
                msg = self.inbox.get(timeout=20)
            except Empty:
                raise AssertionError(f"no reply to {method}") from None
            if msg.get("id") == mid and "method" not in msg:
                return msg["result"]
            if "method" in msg and "id" in msg:  # ruff asking the editor (configuration, ...)
                self.send({"id": msg["id"], "result": None})

    def close(self):
        self.proc.kill()


@pytest.fixture
def client(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 40\n")
    c = Client(tmp_path)
    init = c.request(
        "initialize",
        {"processId": None, "rootUri": tmp_path.as_uri(), "capabilities": {"textDocument": {"diagnostic": {}}}},
    )
    c.caps = init["capabilities"]
    c.notify("initialized", {})
    yield c
    c.close()


def open_py(client: Client, path: Path, text: str) -> str:
    uri = path.as_uri()
    client.notify(
        "textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": text}}
    )
    return uri


def edited(text: str, edits: list[dict]) -> str:
    assert len(edits) == 1 and edits[0]["range"]["start"] == {"line": 0, "character": 0}
    return edits[0]["newText"]


def test_capabilities(client):
    kinds = client.caps["codeActionProvider"]["codeActionKinds"]
    assert "source.fixAll.ruff" in kinds and not any(k.startswith("notebook.") for k in kinds)
    assert "executeCommandProvider" not in client.caps


def test_format_keeps_grids_and_follows_edits(client, tmp_path):
    uri = open_py(client, tmp_path / "a.py", "s = [1, 2, 3,\n")
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [
                {
                    "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 0}},
                    "text": "  4, 5,]\n",
                }
            ],
        },
    )
    out = client.request("textDocument/formatting", {"textDocument": {"uri": uri}, "options": {"tabSize": 4}})
    assert edited("", out) == "s = [\n    1, 2, 3,\n    4, 5,\n]\n"


def test_fix_all_and_organize_imports(client, tmp_path):
    uri = open_py(client, tmp_path / "a.py", "import sys\nimport os\n\nprint(os)\n")
    ask = lambda only: client.request(
        "textDocument/codeAction",
        {
            "textDocument": {"uri": uri},
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            "context": {"diagnostics": [], "only": [only]},
        },
    )
    (fix,) = ask("source.fixAll")
    assert fix["kind"] == "source.fixAll.ruff"
    assert fix["edit"]["changes"][uri][0]["newText"] == "import os\n\nprint(os)\n"
    (org,) = ask("source.organizeImports.ruff")
    assert org["edit"]["changes"][uri][0]["newText"] == "import os\nimport sys\n\nprint(os)\n"


def test_diagnostics_come_from_ruff(client, tmp_path):
    uri = open_py(client, tmp_path / "a.py", "import os\n")
    report = client.request("textDocument/diagnostic", {"textDocument": {"uri": uri}})
    assert [d["code"] for d in report["items"]] == ["F401"]


def test_notebook_actions_run_from_the_first_code_cell_after_markdown(client, tmp_path):
    nb = (tmp_path / "n.ipynb").as_uri()
    md, a, b = (f"vscode-notebook-cell:{tmp_path / 'n.ipynb'}#{k}" for k in ("W0", "W1", "W2"))
    client.notify(
        "notebookDocument/didOpen",
        {
            "notebookDocument": {
                "uri": nb,
                "notebookType": "jupyter-notebook",
                "version": 1,
                "cells": [{"kind": 1, "document": md}, {"kind": 2, "document": a}, {"kind": 2, "document": b}],
            },
            # VS Code sends only the cells in the selector's language: not the markdown one
            "cellTextDocuments": [
                {"uri": a, "languageId": "python", "version": 1, "text": "import sys\nimport os"},
                {"uri": b, "languageId": "python", "version": 1, "text": "x = [1, 2,\n  3,]\nprint(os)"},
            ],
        },
    )

    def ask(only):
        return client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": a},
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
                "context": {"diagnostics": [], "only": [only]},
            },
        )

    (org,) = ask("notebook.source.organizeImports.ruff")
    assert org["edit"]["changes"] == {
        a: [
            {
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 9}},
                "newText": "import os\nimport sys",
            }
        ]
    }
    (fix,) = ask("notebook.source.fixAll")  # os is used in the next cell: only sys goes
    assert fix["edit"]["changes"][a][0]["newText"] == "import os"
    (fmt,) = ask("notebook.format.ruff")
    assert fmt["edit"]["changes"][b][0]["newText"] == "x = [\n    1, 2,\n    3,\n]\nprint(os)"
    assert ask("source.fixAll") == []  # a cell alone: the notebook-wide action does it
