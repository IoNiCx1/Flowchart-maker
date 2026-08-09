# codeanim — animate any Python script's execution with Manim

Paste your code into `target.py`, run one command, get a Manim animation
of it actually *running*: a synced code panel with a moving line
highlight, and every variable rendered live — arrays as rows of boxes,
2D lists as grids, dicts as key/value rows, scalars as labelled boxes.
Long arrays automatically collapse the middle into a single `...` box
instead of listing every element.

This intentionally does **not** draw a flowchart (that's your other repo,
[Flowchart-maker](https://github.com/IoNiCx1/Flowchart-maker)). This one
watches the code *execute* and animates the data.

## Why ManimCE, not 3b1b/manim

You linked `3b1b/manim` — that's 3Blue1Brown's personal repo. It's not
published on PyPI, has no stable public API/docs, and its `main` branch
changes to fit his own videos, so builds break unpredictably. The
community fork, **Manim Community Edition (ManimCE)**, is the
actively-maintained, pip-installable, documented version almost everyone
actually builds on (`pip install manim`) — it's what's used below. The
scene code is plain Manim (`Scene`, `Code`, `Square`, `Transform`, ...),
so if you specifically want to run it against 3b1b/manim's API later,
only `render.py` would need adjusting — `tracer.py` (the part that
understands your code) doesn't touch Manim at all.

## How it works

1. **`tracer.py`** — runs `target.py` under `sys.settrace`, and after
   every executed line, snapshots every local variable that's
   "visualisable" (numbers, strings, lists/tuples, 2D lists, dicts,
   sets — functions/modules/custom objects are skipped). It writes this
   as `trace.json`: `{source_lines, steps: [{lineno, code, vars,
   highlight}, ...]}`. Pure stdlib, no Manim dependency.
2. **`render.py`** — a **generic** Manim `Scene` that reads
   `trace.json` and animates it. It has zero knowledge of your specific
   script; it just turns snapshot dicts into `VGroup`s and
   `Transform`s between consecutive steps. This is what makes it work
   on *any* code, not just arrays — same renderer, whatever you paste in.
3. **`codeanim.py`** — one-command wrapper: runs the tracer, then
   invokes `manim` on `render.py`.

## Setup

```bash
pip install manim --break-system-packages
# Manim also needs: ffmpeg, and Pango/Cairo (for text rendering)
#   Debian/Ubuntu: sudo apt install ffmpeg libpango1.0-dev libcairo2-dev pkg-config
#   macOS:         brew install ffmpeg pango
# (Manim's own install guide has the full per-OS list: docs.manim.community/en/stable/installation.html)
```

## Usage

Paste/replace the contents of `target.py` with whatever you want
animated, then:

```bash
python3 codeanim.py target.py                    # quick 480p preview
python3 codeanim.py target.py --quality h -p      # 1080p, opens the video when done
python3 codeanim.py target.py --max-elements 12   # show up to 12 boxes before truncating
python3 codeanim.py target.py --max-steps 800     # for longer-running scripts
```

Output video: `media/videos/render/<quality>/CodeAnimation.mp4`

Or run the two stages yourself:

```bash
python3 tracer.py target.py -o trace.json     # step 1: no manim needed
manim -pql render.py CodeAnimation            # step 2: renders trace.json
```

### Example

```python
arr = [5, 3, 8, 1, 9]
n = len(arr)
for i in range(n):
    for j in range(0, n - i - 1):
        if arr[j] > arr[j + 1]:
            arr[j], arr[j + 1] = arr[j + 1], arr[j]
print(arr)
```

renders as a live bubble sort: `arr` as 5 numbered boxes, `n`, `i`, `j`
as labelled scalar boxes, the compared cell highlighted in yellow, and
the code panel's highlight tracking every line as it executes —
swaps morph the boxes into their new order.

### Big arrays

```python
data = [i * 2 for i in range(20)]
```

renders `data` as `[0, 2, 4, 6, 8, ..., 30, 32, 34, 36, 38]` — first
half, a single `...` cell, last half — instead of 20 boxes, controlled
by `--max-elements` (default 10).

## What's supported

| Python value | Rendered as |
|---|---|
| `int`, `float`, `bool`, `str`, `None` | `name = value` box |
| `list` / `tuple` of primitives | row of boxes, indices below, truncates with `...` |
| `list` of `list`s of primitives | grid of boxes (2D array / matrix) |
| `dict` of primitives | stacked `key : value` rows |
| `set` / `frozenset` of primitives | row of boxes (unordered, index shown as `*`) |
| `arr[i]` / `arr[expr]` on the current line | that cell highlighted yellow |
| functions, classes, modules, file handles, etc. | intentionally skipped (not visualisable) |

Works on any script: multiple functions, recursion, nested loops,
classes — the tracer follows execution wherever it goes in `target.py`
(nested function calls in the same file are traced too). It doesn't
follow into imported library code, and it caps how many lines it
records (`--max-steps`, default 400) so an infinite/huge loop can't
make the animation infinite — it'll animate the first part and tell you
it hit the limit.

## Known limitations / good next steps

- One global `Transform` per step morphs the *whole* variable panel at
  once — visually clean and fully generic, but a granular
  "only this one box moves/highlights" choreography (like real
  sorting-visualizer videos) would need per-cell diffing between steps.
  That's the natural next upgrade if you want more polished swap/shift
  animations.
- `input()` in `target.py` will hang the tracer (it's genuinely waiting
  on stdin) — pipe input in, or stub it out, if your script needs it.
- Very deep recursion or huge loops: raise `--max-steps`, but render
  time scales with step count, so there's a real tradeoff.
