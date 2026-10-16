# Fix all and organize imports on save (editor.codeActionsOnSave). Save, then `git diff` it.
# - fix all removes `re`: nothing uses it
# - organize imports sorts the block; the `from os.path import (` grid is sorted and packed again,
#   3 names on the first line (Ruff alone: one name per line)
import json
import sys
from os.path import (
    basename, dirname, exists,
    isdir, join, splitext,
)

print(sys.argv, json.dumps({}))
print(join("a", "b"), splitext("x.py"), basename("a/x"), exists("x"), dirname("a/x"), isdir("a"))
