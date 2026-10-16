const fs = require("fs");
const path = require("path");
const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");

let client;

function onPath(name) {
  return (process.env.PATH || "").split(path.delimiter).some((d) => d && fs.existsSync(path.join(d, name)));
}

function serverCommand(cfg, cwd) {
  const custom = cfg.get("serverCommand");
  if (custom && custom.length) return custom;
  const local = cwd && path.join(cwd, ".venv", "bin", "ruffgrid");
  if (local && fs.existsSync(local)) return [local];
  return onPath("ruffgrid") ? ["ruffgrid"] : null;
}

async function start() {
  const cfg = vscode.workspace.getConfiguration("ruffgrid");
  const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const cmd = serverCommand(cfg, cwd);
  if (!cmd) {
    vscode.window.showWarningMessage("ruffgrid: no `ruffgrid` server found (uv tool install <path to ruffgrid>, or set ruffgrid.serverCommand)");
    return;
  }
  const [command, ...args] = cmd;
  client = new LanguageClient(
    "ruffgrid",
    "ruffgrid",
    { command, args, options: { cwd } },
    {
      documentSelector: [
        { scheme: "file", language: "python" },
        { scheme: "untitled", language: "python" },
        { notebook: "jupyter-notebook", language: "python" },
      ],
      // the ruff.* settings go to `ruff server` as they do from the Ruff extension (e.g. ruff.lint.enable:
      // false for no warnings); format and fixes on save use the project's config only
      initializationOptions: { settings: JSON.parse(JSON.stringify(vscode.workspace.getConfiguration("ruff"))) },
    }
  );
  await client.start();
}

// Notebook actions on save (notebook.format, notebook.source.fixAll / organizeImports): VS Code asks for them
// once, on the notebook's first cell, which may be markdown, outside the server's selector. So they're offered
// here for every cell, and asked of the server on the first code cell; the edit covers every code cell
const NOTEBOOK_KINDS = ["format", "source.fixAll", "source.organizeImports"].map((k) =>
  vscode.CodeActionKind.Notebook.append(k).append("ruff")
);

async function notebookAction(document, range, context, token) {
  const kind = client && context.only && NOTEBOOK_KINDS.find((k) => context.only.contains(k));
  if (!kind) return [];
  const uri = document.uri.toString();
  const nb = vscode.workspace.notebookDocuments.find((n) => n.getCells().some((c) => c.document.uri.toString() === uri));
  const cell = nb?.getCells().find((c) => c.kind === vscode.NotebookCellKind.Code && c.document.languageId === "python");
  if (!cell) return [];
  const zero = { line: 0, character: 0 };
  const result = await client.sendRequest(
    "textDocument/codeAction",
    {
      textDocument: { uri: client.code2ProtocolConverter.asUri(cell.document.uri) },
      range: { start: zero, end: zero },
      context: { diagnostics: [], only: [kind.value], triggerKind: 2 },
    },
    token
  );
  return client.protocol2CodeConverter.asCodeActionResult(result || [], token);
}

exports.activate = async (context) => {
  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      { notebookType: "jupyter-notebook" },
      { provideCodeActions: notebookAction },
      { providedCodeActionKinds: NOTEBOOK_KINDS }
    )
  );
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (!e.affectsConfiguration("ruffgrid") && !e.affectsConfiguration("ruff")) return;
      await client?.stop();
      client = undefined;
      await start();
    })
  );
  await start();
};

exports.deactivate = () => client?.stop();
