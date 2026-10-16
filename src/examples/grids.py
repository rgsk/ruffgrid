# Grids. Save this file (format on save, ruffgrid as the formatter), then `git diff` it:
# each case says what Ruff alone would write. `git checkout src/examples` undoes the save.
#
# If the first line inside a split bracket holds several items, the later lines are packed up to
# that line's width (in characters, as Ruff writes it) instead of one item per line.


# 1. The first line is `11, 13, 17` (10 wide): the rest are packed to 10, so 3 to a line here.
#    Ruff alone: one number per line.
primes = [
    11, 13, 17,
    19, 23, 29, 31, 37, 41, 43,
    47,
]


# 2. The first line can start right after the bracket: it moves onto a line of its own.
dirs = [(0, 1), (0, -1),
        (1, 0), (-1, 0),]


# 3. Width, not count: `10, 20, 30` is 10 wide, so a wider item gets a line to itself.
def bfs(n, m, k, start, blocked):
    return n, m, k, start, blocked


result = bfs(
    10, 20, 30,
    start=(0, 0), blocked={(1, 1), (2, 2)},
)


# 4. Nested: each bracket is its own grid.
board = {
    "empty": [
        0, 0, 0,
        0, 0, 0, 0, 0, 0,
    ],
    "cross": [1, 0, 1,
              0, 1, 0, 1, 0, 1,],
}


# 5. The width is measured after Ruff spaces the first line, so `10,20,30` counts as `10, 20, 30`.
tens = [
    10,20,30,
    40,50,60,70,80,90,
]


# 6. One item on the first line: Ruff's layout, one per line.
names = [
    "ada",
    "grace", "linus",
]


# 7. No trailing comma after the last item: Ruff's layout again, here joined onto one line.
short = [
    1, 2, 3,
    4
]


# 8. Ruff's `# fmt: off` / `# fmt: on` (and `# fmt: skip`) still apply.
# fmt: off
identity = [
    1,  0,  0,
    0,  1,  0,
    0,  0,  1,
]
# fmt: on
