"""Ruff with grids, on a .py file and on notebook cells (the real ruff, run in tmp_path)."""

from pathlib import Path

import pytest

from ruffgrid.grid import ORGANIZE, fix_cells, fix_py, format_cells, format_py, resolve_ruff

RUFF = resolve_ruff()


@pytest.fixture
def py(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 40\n")
    return tmp_path / "a.py"


def test_a_grid_is_packed_to_its_first_line(py):
    src = "s = [1, 2, 3,\n  4, 5, 6, 7, 8, 9, 10,]\n"
    out = format_py(src, py, RUFF)
    assert out == "s = [\n    1, 2, 3,\n    4, 5, 6,\n    7, 8, 9,\n    10,\n]\n"
    assert format_py(out, py, RUFF) == out  # a second format changes nothing


def test_one_item_on_the_first_line_is_ruffs_layout(py):
    assert format_py("f(\n    a,\n    b, c,\n)\n", py, RUFF) == "f(\n    a,\n    b,\n    c,\n)\n"


def test_the_projects_config_is_read(py):
    long = "x = some_function(argument_one, argument_two)\n"  # 46 > 40
    assert format_py(long, py, RUFF) == "x = some_function(\n    argument_one, argument_two\n)\n"


def test_organize_imports_keeps_an_import_grid(py):
    src = "from os import (\n    sep, path, name,\n    getcwd,\n)\nimport sys\n\nprint(sep, path, name, getcwd)\n"
    out = fix_py(src, py, RUFF, ORGANIZE)
    assert (
        out == "import sys\nfrom os import (\n    getcwd, name, path,\n    sep,\n)\n\nprint(sep, path, name, getcwd)\n"
    )


def test_fix_all_removes_an_unused_import(py):
    assert fix_py("import os\nimport sys\n\nprint(sys)\n", py, RUFF) == "import sys\n\nprint(sys)\n"


def test_cells_are_formatted_as_notebook_cells(tmp_path):
    nb = tmp_path / "n.ipynb"
    cells = ["%time x=1\ns = [\n    1, 2, 3,\n    4, 5,\n]", "y=(", "z=1\n"]
    # a magic is fine; a cell that doesn't parse is left as it is, the others still formatted;
    # as Ruff writes notebooks, a cell's last line has no newline
    assert format_cells(cells, nb, RUFF) == ["%time x=1\ns = [\n    1, 2, 3,\n    4, 5,\n]", "y=(", "z = 1"]


def test_a_notebook_is_fixed_whole(tmp_path):
    nb = tmp_path / "n.ipynb"
    # os is used in the second cell, so it stays; sys is unused
    assert fix_cells(["import os\nimport sys", "print(os.sep)"], nb, RUFF) == ["import os", "print(os.sep)"]


def test_a_notebook_that_doesnt_parse_is_left_as_it_is(tmp_path):
    cells = ["import os, sys", "x = ("]
    assert fix_cells(cells, tmp_path / "n.ipynb", RUFF) == cells


def test_a_file_the_config_excludes_is_left_as_it_is(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.ruff]\nextend-exclude = ["vendored.py"]\n')
    src = "import os\nx=[1,2,\n 3,]\n"
    assert format_py(src, tmp_path / "vendored.py", RUFF) == src
    assert fix_py(src, tmp_path / "vendored.py", RUFF) == src
    nb = tmp_path / "vendored.ipynb"
    (tmp_path / "pyproject.toml").write_text('[tool.ruff]\nextend-exclude = ["vendored.py", "vendored.ipynb"]\n')
    assert format_cells(["x=[1,2,\n 3,]"], nb, RUFF) == ["x=[1,2,\n 3,]"]
    assert fix_cells(["import os"], nb, RUFF) == ["import os"]


def test_a_relative_path_finds_the_projects_config(py, monkeypatch):
    monkeypatch.chdir(py.parent.parent)
    long = "x = some_function(argument_one, argument_two)\n"  # 46 > 40
    assert (
        format_py(long, Path(py.parent.name) / "a.py", RUFF)
        == "x = some_function(\n    argument_one, argument_two\n)\n"
    )
