#!/usr/bin/env python3
"""
py2flowchart.py
================
Convert Python source code into flowchart images (PNG/SVG).

Works on any .py file: plain scripts, single functions, multiple functions,
classes with methods, loops, conditionals, try/except, break/continue,
recursion, etc. Each top-level function/method gets its own flowchart image
so complex files stay readable; module-level ("script") code gets a
"main" flowchart.

Usage:
    python3 py2flowchart.py solution.py
    python3 py2flowchart.py solution.py -o mychart --format svg
    python3 py2flowchart.py solution.py --function twoSum   # only one function

Requires:
    pip install graphviz --break-system-packages
    (and the system 'dot' binary from Graphviz, e.g. `apt install graphviz`)

As a library:
    from py2flowchart import PythonFlowchartConverter
    conv = PythonFlowchartConverter()
    paths = conv.convert_source(code_string, output_prefix="out")
"""

import ast
import argparse
import textwrap
import sys
import os

try:
    from graphviz import Digraph
except ImportError:
    print("Missing dependency. Install with: pip install graphviz --break-system-packages")
    sys.exit(1)


# ----------------------------------------------------------------------
# Visual style constants
# ----------------------------------------------------------------------
COLOR_START_END = "#C8E6C9"   # green
COLOR_END = "#FFCDD2"         # red
COLOR_PROCESS = "#EAF2FB"     # light blue
COLOR_DECISION = "#FFF3CD"    # yellow
COLOR_IO = "#D8F3DC"          # light green (print/input)
COLOR_RETURN = "#F8BBD0"      # pink
COLOR_BREAK_CONT = "#FFE0B2"  # orange
COLOR_EXCEPTION = "#F5C6CB"   # rose
COLOR_DEF = "#E2E3E5"         # gray
WRAP_WIDTH = 42


class FlowchartBuilder:
    """
    Builds one Graphviz Digraph representing the flowchart of a single
    function (or the module-level script body).

    The core idea: `process_block` walks a list of statements and returns
    a "frontier" -- a list of (node_id, edge_label) pairs representing all
    the currently-open/dangling execution paths that still need to be
    connected to whatever comes next (this is how if/else branches merge
    back together, and how loops/break/continue/return are wired up).
    """

    def __init__(self, name="flowchart", rankdir="TB"):
        self.dot = Digraph(name=name)
        self.dot.attr(rankdir=rankdir, splines="polyline", nodesep="0.45", ranksep="0.55")
        self.dot.attr("node", fontname="Helvetica", fontsize="11")
        self.dot.attr("edge", fontname="Helvetica", fontsize="9")
        self._counter = 0

        # Stacks for handling control flow that jumps out of normal sequence
        self.loop_break_stack = []      # list of lists: collected break-frontiers per loop
        self.loop_continue_stack = []   # list of node ids: the loop's condition node
        self.func_return_stack = []     # list of node ids: the function's End node

    # -- helpers ---------------------------------------------------------
    def _new_id(self):
        self._counter += 1
        return f"n{self._counter}"

    @staticmethod
    def _wrap(text, width=WRAP_WIDTH):
        text = str(text)
        wrapped = "\n".join(textwrap.wrap(text, width)) if len(text) > width else text
        return wrapped if wrapped else text

    @staticmethod
    def _src(node):
        """Render an AST node back to readable source text."""
        try:
            return ast.unparse(node)
        except Exception:
            return type(node).__name__

    def add_node(self, label, shape="box", fillcolor=COLOR_PROCESS):
        nid = self._new_id()
        style = "filled" if shape in ("diamond", "oval", "parallelogram") else "filled,rounded"
        self.dot.node(nid, label=self._wrap(label), shape=shape, style=style, fillcolor=fillcolor)
        return nid

    def connect_frontier(self, frontier, target, extra_style=None):
        """Connect every dangling (node_id, label) pair in `frontier` to `target`."""
        for nid, label in frontier:
            kwargs = {}
            if label:
                kwargs["label"] = label
                if label.lower().startswith("yes") or label.lower().startswith("true"):
                    kwargs["color"] = "#2E7D32"
                elif label.lower().startswith("no") or label.lower().startswith("false"):
                    kwargs["color"] = "#C62828"
            if extra_style:
                kwargs.update(extra_style)
            self.dot.edge(nid, target, **kwargs)

    # -- block / statement dispatch --------------------------------------
    def process_block(self, stmts, frontier):
        """Process a list of statements, threading the frontier through them."""
        for stmt in stmts:
            frontier = self.process_stmt(stmt, frontier)
            if not frontier:
                break  # remaining statements in this block are unreachable
        return frontier

    def process_stmt(self, stmt, frontier):
        if isinstance(stmt, ast.If):
            return self._process_if(stmt, frontier)
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            return self._process_for(stmt, frontier)
        if isinstance(stmt, ast.While):
            return self._process_while(stmt, frontier)
        if isinstance(stmt, ast.Try):
            return self._process_try(stmt, frontier)
        if isinstance(stmt, ast.With):
            return self._process_with(stmt, frontier)
        if isinstance(stmt, ast.Return):
            return self._process_return(stmt, frontier)
        if isinstance(stmt, ast.Break):
            return self._process_break(frontier)
        if isinstance(stmt, ast.Continue):
            return self._process_continue(frontier)
        if isinstance(stmt, ast.Raise):
            return self._process_raise(stmt, frontier)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nid = self.add_node(f"def {stmt.name}(...)  [see separate chart]", fillcolor=COLOR_DEF)
            self.connect_frontier(frontier, nid)
            return [(nid, None)]
        if isinstance(stmt, ast.Assert):
            label = "Assert " + self._src(stmt.test)
            nid = self.add_node(label, shape="diamond", fillcolor=COLOR_DECISION)
            self.connect_frontier(frontier, nid)
            return [(nid, "pass")]
        if isinstance(stmt, ast.Pass):
            nid = self.add_node("pass", fillcolor="#F0F0F0")
            self.connect_frontier(frontier, nid)
            return [(nid, None)]
        if isinstance(stmt, ast.Expr):
            return self._process_expr(stmt, frontier)
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            nid = self.add_node(self._src(stmt), fillcolor=COLOR_DEF)
            self.connect_frontier(frontier, nid)
            return [(nid, None)]
        # Assign / AugAssign / AnnAssign / anything else generic
        nid = self.add_node(self._src(stmt), fillcolor=COLOR_PROCESS)
        self.connect_frontier(frontier, nid)
        return [(nid, None)]

    # -- individual statement handlers -----------------------------------
    def _process_expr(self, stmt, frontier):
        call_name = None
        if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name):
            call_name = stmt.value.func.id
        label = self._src(stmt)
        if call_name in ("print",):
            nid = self.add_node(label, shape="parallelogram", fillcolor=COLOR_IO)
        else:
            nid = self.add_node(label, fillcolor=COLOR_PROCESS)
        self.connect_frontier(frontier, nid)
        return [(nid, None)]

    def _process_return(self, stmt, frontier):
        label = "Return" if stmt.value is None else f"Return {self._src(stmt.value)}"
        nid = self.add_node(label, shape="box", fillcolor=COLOR_RETURN)
        self.connect_frontier(frontier, nid)
        if self.func_return_stack:
            self.dot.edge(nid, self.func_return_stack[-1])
        return []  # this path terminates

    def _process_break(self, frontier):
        nid = self.add_node("break", fillcolor=COLOR_BREAK_CONT)
        self.connect_frontier(frontier, nid)
        if self.loop_break_stack:
            self.loop_break_stack[-1].append((nid, None))
        return []

    def _process_continue(self, frontier):
        nid = self.add_node("continue", fillcolor=COLOR_BREAK_CONT)
        self.connect_frontier(frontier, nid)
        if self.loop_continue_stack:
            self.dot.edge(nid, self.loop_continue_stack[-1], style="dashed")
        return []

    def _process_raise(self, stmt, frontier):
        label = "raise " + (self._src(stmt.exc) if stmt.exc else "")
        nid = self.add_node(label, fillcolor=COLOR_EXCEPTION)
        self.connect_frontier(frontier, nid)
        return []

    def _process_if(self, stmt, frontier):
        cond_id = self.add_node(self._src(stmt.test), shape="diamond", fillcolor=COLOR_DECISION)
        self.connect_frontier(frontier, cond_id)

        true_frontier = self.process_block(stmt.body, [(cond_id, "Yes")])
        if stmt.orelse:
            false_frontier = self.process_block(stmt.orelse, [(cond_id, "No")])
        else:
            false_frontier = [(cond_id, "No")]
        return true_frontier + false_frontier

    def _process_for(self, stmt, frontier):
        label = f"for {self._src(stmt.target)} in {self._src(stmt.iter)}"
        cond_id = self.add_node(label, shape="diamond", fillcolor=COLOR_DECISION)
        self.connect_frontier(frontier, cond_id)

        self.loop_break_stack.append([])
        self.loop_continue_stack.append(cond_id)

        body_frontier = self.process_block(stmt.body, [(cond_id, "Yes (next item)")])
        if body_frontier:
            self.connect_frontier(body_frontier, cond_id, extra_style={"style": "dashed"})

        break_frontiers = self.loop_break_stack.pop()
        self.loop_continue_stack.pop()

        if stmt.orelse:
            else_frontier = self.process_block(stmt.orelse, [(cond_id, "No (done)")])
            return else_frontier + break_frontiers
        return [(cond_id, "No (done)")] + break_frontiers

    def _process_while(self, stmt, frontier):
        cond_id = self.add_node(self._src(stmt.test), shape="diamond", fillcolor=COLOR_DECISION)
        self.connect_frontier(frontier, cond_id)

        self.loop_break_stack.append([])
        self.loop_continue_stack.append(cond_id)

        body_frontier = self.process_block(stmt.body, [(cond_id, "Yes")])
        if body_frontier:
            self.connect_frontier(body_frontier, cond_id, extra_style={"style": "dashed"})

        break_frontiers = self.loop_break_stack.pop()
        self.loop_continue_stack.pop()

        if stmt.orelse:
            else_frontier = self.process_block(stmt.orelse, [(cond_id, "No")])
            return else_frontier + break_frontiers
        return [(cond_id, "No")] + break_frontiers

    def _process_try(self, stmt, frontier):
        body_frontier = self.process_block(stmt.body, frontier)
        exit_frontiers = []

        # Exceptions can be considered to occur anywhere in the try body,
        # so branch from the try's entry point to each handler.
        for handler in stmt.handlers:
            exc_type = self._src(handler.type) if handler.type else "Exception"
            exc_id = self.add_node(f"except {exc_type}", shape="diamond", fillcolor=COLOR_EXCEPTION)
            self.connect_frontier(frontier, exc_id, extra_style={"style": "dashed", "label": "on error"})
            handler_frontier = self.process_block(handler.body, [(exc_id, None)])
            exit_frontiers += handler_frontier

        if stmt.orelse and body_frontier:
            body_frontier = self.process_block(stmt.orelse, body_frontier)

        exit_frontiers = body_frontier + exit_frontiers

        if stmt.finalbody:
            finally_id = self.add_node("finally", fillcolor=COLOR_DEF)
            self.connect_frontier(exit_frontiers, finally_id)
            exit_frontiers = self.process_block(stmt.finalbody, [(finally_id, None)])

        return exit_frontiers

    def _process_with(self, stmt, frontier):
        items = ", ".join(self._src(i.context_expr) for i in stmt.items)
        nid = self.add_node(f"with {items}", fillcolor=COLOR_DEF)
        self.connect_frontier(frontier, nid)
        return self.process_block(stmt.body, [(nid, None)])


class PythonFlowchartConverter:
    """High-level entry point: turns Python source into one or more rendered
    flowchart image files (one per function/method, plus one for any
    top-level script code)."""

    def __init__(self, fmt="png", rankdir="TB"):
        self.fmt = fmt
        self.rankdir = rankdir

    def _build_function_chart(self, func_node, display_name=None):
        name = display_name or func_node.name
        builder = FlowchartBuilder(name=name, rankdir=self.rankdir)
        args = ", ".join(
            a.arg for a in (
                func_node.args.posonlyargs + func_node.args.args
                if hasattr(func_node.args, "posonlyargs") else func_node.args.args
            )
        )
        start_id = builder.add_node(f"Start\n{func_node.name}({args})", shape="oval", fillcolor=COLOR_START_END)
        end_id = builder.add_node(f"End\n{func_node.name}", shape="oval", fillcolor=COLOR_END)

        builder.func_return_stack.append(end_id)
        frontier = builder.process_block(func_node.body, [(start_id, None)])
        builder.connect_frontier(frontier, end_id)
        builder.func_return_stack.pop()

        builder.dot.format = self.fmt
        return builder.dot

    def _build_script_chart(self, stmts, name="main"):
        builder = FlowchartBuilder(name=name, rankdir=self.rankdir)
        start_id = builder.add_node("Start", shape="oval", fillcolor=COLOR_START_END)
        end_id = builder.add_node("End", shape="oval", fillcolor=COLOR_END)
        frontier = builder.process_block(stmts, [(start_id, None)])
        builder.connect_frontier(frontier, end_id)
        builder.dot.format = self.fmt
        return builder.dot

    def convert_source(self, code, output_prefix="flowchart", only_function=None, outdir="."):
        """
        Parse `code` and render a flowchart image for every function/method
        found (and one for top-level script statements, if any).

        Returns a list of the rendered file paths.
        """
        tree = ast.parse(code)
        outputs = []
        leftover_stmts = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if only_function and node.name != only_function:
                    continue
                dot = self._build_function_chart(node)
                path = dot.render(
                    filename=f"{output_prefix}_{node.name}",
                    directory=outdir, cleanup=True
                )
                outputs.append(path)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if only_function and item.name != only_function:
                            continue
                        dot = self._build_function_chart(item, display_name=f"{node.name}.{item.name}")
                        path = dot.render(
                            filename=f"{output_prefix}_{node.name}_{item.name}",
                            directory=outdir, cleanup=True
                        )
                        outputs.append(path)
            else:
                leftover_stmts.append(node)

        if leftover_stmts and not only_function:
            dot = self._build_script_chart(leftover_stmts)
            path = dot.render(filename=f"{output_prefix}_main", directory=outdir, cleanup=True)
            outputs.append(path)

        if not outputs:
            raise ValueError(
                "Nothing to render â€” no functions/classes found "
                "(and no top-level statements outside of them)."
            )
        return outputs


def main():
    parser = argparse.ArgumentParser(description="Convert Python code into flowchart images.")
    parser.add_argument("input", help="Path to a .py file to convert")
    parser.add_argument("-o", "--output", default="flowchart", help="Output filename prefix")
    parser.add_argument("--outdir", default=".", help="Output directory")
    parser.add_argument("--format", default="png", choices=["png", "svg", "pdf"], help="Image format")
    parser.add_argument("--function", default=None, help="Only render this one function/method by name")
    parser.add_argument("--rankdir", default="TB", choices=["TB", "LR"], help="Flowchart direction")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        code = f.read()

    try:
        tree_check = ast.parse(code)  # validate syntax early with a clear error message
    except SyntaxError as e:
        print(f"Error: could not parse '{args.input}' — invalid Python syntax.")
        print(f"  Line {e.lineno}: {e.text.strip() if e.text else ''}")
        print(f"  {e.msg}")
        sys.exit(1)

    converter = PythonFlowchartConverter(fmt=args.format, rankdir=args.rankdir)
    try:
        paths = converter.convert_source(code, output_prefix=args.output, only_function=args.function, outdir=args.outdir)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Generated {len(paths)} flowchart(s):")
    for p in paths:
        print(" -", p)


if __name__ == "__main__":
    main()