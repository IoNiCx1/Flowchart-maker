#!/usr/bin/env python3
"""
tracer.py
=========
Runs a target Python script under sys.settrace and records, line by line,
a JSON-serialisable snapshot of every "visualisable" variable in scope
(numbers, strings, lists/tuples, 2D lists, dicts, sets).

The output (trace.json) is consumed by render.py, a generic Manim scene
that turns the trace into an animation. tracer.py has no dependency on
manim at all -- it's pure stdlib, so it works even before manim/its
system deps (pango/cairo) are installed.

Usage:
    python3 tracer.py target.py -o trace.json --max-steps 400
"""

import argparse
import ast
import json
import os
import sys
import types

MAX_STEPS_DEFAULT = 400
MAX_LINE_EVENTS_DEFAULT = 20000


class _StepLimitReached(Exception):
    pass


# ----------------------------------------------------------------------
# Value -> JSON-safe "visual snapshot" classification
# ----------------------------------------------------------------------
def _is_primitive(x):
    return isinstance(x, (int, float, str, bool)) or x is None


def _prim_str(x):
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, str):
        return x if len(x) <= 20 else x[:17] + "..."
    if isinstance(x, float):
        return f"{x:.4g}"
    if x is None:
        return "None"
    return str(x)


def _short_repr(v, limit=40):
    try:
        r = repr(v)
    except Exception:
        r = f"<{type(v).__name__}>"
    return r if len(r) <= limit else r[: limit - 3] + "..."


def snap_value(v):
    """Turn an arbitrary Python value into a small JSON-safe dict describing
    how it should be drawn: scalar / array / array2d / dict. Returns None
    for things we deliberately never visualise (functions, modules, types,
    imported packages, file handles, etc.)."""
    if isinstance(v, (types.ModuleType, types.FunctionType,
                       types.BuiltinFunctionType, types.MethodType, type)):
        return None
    if isinstance(v, bool):
        return {"type": "scalar", "value": str(v)}
    if isinstance(v, (int, float)):
        return {"type": "scalar", "value": _prim_str(v)}
    if v is None:
        return {"type": "scalar", "value": "None"}
    if isinstance(v, str):
        shown = v if len(v) <= 30 else v[:27] + "..."
        return {"type": "scalar", "value": repr(shown)}

    if isinstance(v, (list, tuple)):
        try:
            if (len(v) and all(isinstance(e, (list, tuple)) for e in v)
                    and all(all(_is_primitive(x) for x in row) for row in v)):
                return {"type": "array2d",
                        "value": [[_prim_str(x) for x in row] for row in v]}
            if all(_is_primitive(e) for e in v):
                return {"type": "array", "value": [_prim_str(e) for e in v]}
        except TypeError:
            pass
        return {"type": "scalar", "value": _short_repr(v)}

    if isinstance(v, dict):
        try:
            if len(v) <= 40 and all(_is_primitive(k) for k in v) and \
                    all(_is_primitive(x) for x in v.values()):
                return {"type": "dict",
                        "value": [[_prim_str(k), _prim_str(val)] for k, val in v.items()]}
        except TypeError:
            pass
        return {"type": "scalar", "value": _short_repr(v)}

    if isinstance(v, (set, frozenset)):
        try:
            if all(_is_primitive(e) for e in v):
                return {"type": "array",
                        "value": sorted(_prim_str(e) for e in v),
                        "unordered": True}
        except TypeError:
            pass
        return {"type": "scalar", "value": _short_repr(v)}

    return {"type": "scalar", "value": _short_repr(v)}


def _extract_highlight(line_text, local_snap):
    """Best-effort: if the current line indexes a tracked array/list with a
    literal int or a known scalar variable (e.g. `arr[i]`), return which
    variable/index to highlight in the animation."""
    try:
        tree = ast.parse(line_text.strip() or "pass", mode="exec")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            arr_name = node.value.id
            if arr_name not in local_snap or local_snap[arr_name]["type"] not in ("array", "array2d"):
                continue
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, int):
                return {"var": arr_name, "index": sl.value}
            if isinstance(sl, ast.Name):
                idx_info = local_snap.get(sl.id)
                if idx_info and idx_info["type"] == "scalar":
                    try:
                        return {"var": arr_name, "index": int(idx_info["value"])}
                    except ValueError:
                        continue
    return None


def build_trace(target_path, max_steps=MAX_STEPS_DEFAULT,
                 max_line_events=MAX_LINE_EVENTS_DEFAULT):
    target_path = os.path.abspath(target_path)
    with open(target_path, "r", encoding="utf-8") as f:
        source = f.read()
    source_lines = source.splitlines() or [""]
    ast.parse(source)  # fail fast with a clear SyntaxError if invalid

    steps = []
    counters = {"line_events": 0}

    def snapshot_locals(frame):
        snap = {}
        for name, val in list(frame.f_locals.items()):
            if name.startswith("__"):
                continue
            s = snap_value(val)
            if s is not None:
                snap[name] = s
        return snap

    def local_trace(frame, event, arg):
        if frame.f_code.co_filename != target_path:
            return None
        if event == "line":
            counters["line_events"] += 1
            if counters["line_events"] > max_line_events or len(steps) >= max_steps:
                raise _StepLimitReached()
            lineno = frame.f_lineno
            code_text = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
            snap = snapshot_locals(frame)
            steps.append({
                "lineno": lineno,
                "scope": frame.f_code.co_name,
                "code": code_text,
                "vars": snap,
                "highlight": _extract_highlight(code_text, snap),
            })
        return local_trace

    _SKIP_SCOPES = {"<listcomp>", "<setcomp>", "<dictcomp>", "<genexpr>"}

    def global_trace(frame, event, arg):
        if (event == "call" and frame.f_code.co_filename == target_path
                and frame.f_code.co_name not in _SKIP_SCOPES):
            return local_trace
        return None

    g = {"__name__": "__main__", "__file__": target_path, "__builtins__": __builtins__}
    compiled = compile(source, target_path, "exec")

    truncated = False
    old_trace = sys.gettrace()
    sys.settrace(global_trace)
    try:
        exec(compiled, g)
    except _StepLimitReached:
        truncated = True
    except SystemExit:
        pass
    except Exception as e:
        sys.settrace(old_trace)
        raise RuntimeError(
            f"target script raised {type(e).__name__}: {e} "
            f"(animation covers everything executed up to that point)"
        ) from e
    finally:
        sys.settrace(old_trace)

    if not steps:
        raise ValueError(
            "No traceable statements executed (empty script, or nothing but "
            "function/class definitions that were never called)."
        )

    return {"source_lines": source_lines, "steps": steps, "truncated": truncated}


def main():
    p = argparse.ArgumentParser(description="Trace a Python script's execution for Manim animation.")
    p.add_argument("target", help="Path to the .py file to trace (e.g. target.py)")
    p.add_argument("-o", "--output", default="trace.json", help="Where to write the trace JSON")
    p.add_argument("--max-steps", type=int, default=MAX_STEPS_DEFAULT,
                   help="Max number of executed lines to record (keeps the animation finite)")
    p.add_argument("--max-elements", type=int, default=10,
                   help="Max array cells shown before collapsing the middle into '...'")
    args = p.parse_args()

    if not os.path.isfile(args.target):
        print(f"Error: file not found: {args.target}")
        sys.exit(1)

    try:
        result = build_trace(args.target, max_steps=args.max_steps)
    except SyntaxError as e:
        print(f"Error: could not parse '{args.target}' -- invalid Python syntax.")
        print(f"  Line {e.lineno}: {(e.text or '').strip()}")
        print(f"  {e.msg}")
        sys.exit(1)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    result["max_elements"] = args.max_elements
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)

    msg = f"Traced {len(result['steps'])} step(s) -> {args.output}"
    if result["truncated"]:
        msg += "  (hit --max-steps limit; animation covers the first part of the run)"
    print(msg)


if __name__ == "__main__":
    main()
