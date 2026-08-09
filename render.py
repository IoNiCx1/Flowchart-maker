#!/usr/bin/env python3
"""
render.py
=========
A GENERIC Manim scene. It does not know anything about your specific
target.py -- it just reads trace.json (produced by tracer.py) and
animates it: a code panel with a moving highlight, plus a live panel of
every variable in scope, rendered as boxes (arrays), grids (2D arrays),
key/value rows (dicts) or plain labels (scalars). Long arrays collapse
the middle into a single "..." cell.

Run it with manim, e.g.:
    manim -pql render.py CodeAnimation      # quick preview (480p)
    manim -pqh render.py CodeAnimation      # high quality (1080p)

It expects trace.json to sit next to this file (that's what codeanim.py
sets up for you).
"""

import json
import os

from manim import *

TRACE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trace.json")

BOX_SIDE = 0.8
CELL_BUFF = 0.08
ARRAY_COLOR = BLUE_D
HIGHLIGHT_COLOR = YELLOW
LABEL_COLOR = TEAL_C


# ----------------------------------------------------------------------
# Mobject builders -- turn one snapshot dict into a Manim VGroup
# ----------------------------------------------------------------------
def _fit_text(s, font_size=26, max_width=BOX_SIDE - 0.15):
    t = Text(str(s), font_size=font_size)
    if t.width > max_width:
        t.scale_to_fit_width(max_width)
    return t


def _cell(value, is_ellipsis=False):
    sq = Square(side_length=BOX_SIDE)
    sq.set_stroke(ARRAY_COLOR, 2)
    sq.set_fill(ARRAY_COLOR, opacity=0.12)
    txt = Text("...", font_size=22) if is_ellipsis else _fit_text(value)
    txt.move_to(sq.get_center())
    return VGroup(sq, txt)


def _visible_slice(items, max_elems):
    """Collapse a long list into (shown_values, shown_original_indices)."""
    n = len(items)
    if n <= max_elems:
        return list(items), list(range(n))
    k = max(1, max_elems // 2)
    shown = items[:k] + ["..."] + items[-k:]
    idxs = list(range(k)) + [None] + list(range(n - k, n))
    return shown, idxs


def make_array_mobject(name, values, max_elems, unordered=False, highlight_index=None):
    shown, idxs = _visible_slice(values, max_elems)
    cells = VGroup(*[_cell(v, is_ellipsis=(v == "...")) for v in shown])
    cells.arrange(RIGHT, buff=CELL_BUFF)

    idx_row = VGroup()
    for cell, orig_idx in zip(cells, idxs):
        if orig_idx is not None:
            lbl = Text("*" if unordered else str(orig_idx), font_size=15, color=GRAY_B)
            lbl.next_to(cell, DOWN, buff=0.08)
            idx_row.add(lbl)
        if highlight_index is not None and orig_idx == highlight_index:
            cell[0].set_fill(HIGHLIGHT_COLOR, opacity=0.55)
            cell[0].set_stroke(HIGHLIGHT_COLOR, 4)

    title = Text(name, font_size=24, color=LABEL_COLOR, weight=BOLD)
    body = VGroup(cells, idx_row).arrange(DOWN, buff=0.08)
    return VGroup(title, body).arrange(DOWN, buff=0.12, aligned_edge=LEFT)


def make_grid_mobject(name, rows, max_rows=6, max_cols=8):
    shown_rows = rows[:max_rows]
    grid = VGroup()
    for row in shown_rows:
        shown_cols, _ = _visible_slice(row, max_cols)
        row_group = VGroup(*[_cell(v, is_ellipsis=(v == "...")) for v in shown_cols])
        row_group.arrange(RIGHT, buff=CELL_BUFF)
        grid.add(row_group)
    grid.arrange(DOWN, buff=CELL_BUFF)
    if len(rows) > max_rows:
        more = Text("...", font_size=22)
        grid.add(more)
        grid.arrange(DOWN, buff=CELL_BUFF)
    title = Text(name, font_size=24, color=LABEL_COLOR, weight=BOLD)
    return VGroup(title, grid).arrange(DOWN, buff=0.12, aligned_edge=LEFT)


def make_dict_mobject(name, pairs, max_rows=8):
    shown = pairs[:max_rows]
    rows = VGroup()
    for k, v in shown:
        row = Rectangle(width=3.0, height=0.55, stroke_color=ARRAY_COLOR, fill_color=ARRAY_COLOR, fill_opacity=0.12)
        txt = _fit_text(f"{k} : {v}", font_size=22, max_width=2.8)
        txt.move_to(row.get_center())
        rows.add(VGroup(row, txt))
    if len(pairs) > max_rows:
        rows.add(Text("...", font_size=20))
    rows.arrange(DOWN, buff=0.06)
    title = Text(name, font_size=24, color=LABEL_COLOR, weight=BOLD)
    return VGroup(title, rows).arrange(DOWN, buff=0.12, aligned_edge=LEFT)


def make_scalar_mobject(name, value):
    txt = Text(f"{name} = {value}", font_size=26, color=WHITE)
    box = SurroundingRectangle(txt, color=GRAY_C, buff=0.15, corner_radius=0.08)
    return VGroup(box, txt)


def build_state_group(vars_dict, max_elems, highlight):
    entries = VGroup()
    for name, info in vars_dict.items():
        t = info["type"]
        hl_idx = highlight["index"] if (highlight and highlight.get("var") == name) else None
        if t == "array":
            g = make_array_mobject(name, info["value"], max_elems,
                                    unordered=info.get("unordered", False),
                                    highlight_index=hl_idx)
        elif t == "array2d":
            g = make_grid_mobject(name, info["value"])
        elif t == "dict":
            g = make_dict_mobject(name, info["value"])
        else:
            g = make_scalar_mobject(name, info["value"])
        entries.add(g)
    if len(entries) == 0:
        placeholder = Dot(radius=0.001)
        placeholder.set_opacity(0)
        entries.add(placeholder)
    entries.arrange(DOWN, buff=0.35, aligned_edge=LEFT)
    return entries


# ----------------------------------------------------------------------
# Scene
# ----------------------------------------------------------------------
class CodeAnimation(Scene):
    def construct(self):
        with open(TRACE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        source_lines = data["source_lines"]
        steps = data["steps"]
        max_elems = data.get("max_elements", 10)

        code = Code(
            code_string="\n".join(source_lines) or " ",
            language="python",
            background="window",
            formatter_style="monokai",
            add_line_numbers=True,
        )
        if code.width > 6.0:
            code.scale_to_fit_width(6.0)
        code.to_corner(UL, buff=0.35)
        self.play(FadeIn(code), run_time=0.6)

        highlight_rect = None
        state_group = None

        n_steps = len(steps)
        # keep total runtime sane for very long traces by speeding up mid-run
        base_rt = 0.45 if n_steps <= 120 else max(0.08, 8.0 * 0.45 / n_steps * 12)

        for step in steps:
            idx = step["lineno"] - 1
            if 0 <= idx < len(code.code_lines):
                target_rect = SurroundingRectangle(code.code_lines[idx], color=YELLOW, buff=0.05)
                target_rect.set_stroke(width=2)
                if highlight_rect is None:
                    highlight_rect = target_rect
                    self.play(Create(highlight_rect), run_time=0.25)
                else:
                    self.play(Transform(highlight_rect, target_rect), run_time=min(base_rt, 0.3))

            new_state = build_state_group(step["vars"], max_elems, step.get("highlight"))
            if new_state.width > 6.3:
                new_state.scale_to_fit_width(6.3)
            new_state.next_to(code, RIGHT, buff=0.7).align_to(code, UP)

            if state_group is None:
                state_group = new_state
                self.play(FadeIn(state_group), run_time=0.4)
            else:
                self.play(Transform(state_group, new_state), run_time=base_rt)

            self.wait(0.08)

        self.wait(1.2)
