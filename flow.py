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
COLOR_HASHMAP = "#B2DFDB"     # teal   -- dict / defaultdict / Counter / OrderedDict
COLOR_SET = "#D1C4E9"         # purple -- set / frozenset
COLOR_ARRAY = "#90CAF9"       # blue   -- list / deque
COLOR_HEAP = "#FFCC80"        # orange -- heapq operations
WRAP_WIDTH = 42

# Human names + legend text for each tracked data-structure category
CATEGORY_INFO = {
    "hashmap": ("Hash Map", COLOR_HASHMAP, "Hash Map / dict operation — key lookup, insert, delete ~O(1) avg"),
    "set":     ("Set", COLOR_SET, "Set operation — membership check, add, remove ~O(1) avg"),
    "array":   ("Array", COLOR_ARRAY, "Array / list (or deque) operation"),
    "heap":    ("Heap", COLOR_HEAP, "Heap (priority queue) operation via heapq — push/pop ~O(log n)"),
}

# Calls that CREATE a new instance of a tracked structure, e.g. `x = dict()`
CREATE_HASHMAP_CALLS = {"dict", "defaultdict", "OrderedDict", "Counter"}
CREATE_SET_CALLS = {"set", "frozenset"}
CREATE_ARRAY_CALLS = {"list", "sorted", "deque"}

# Method-name -> plain-English explanation (with rough Big-O), per category.
METHOD_EXPLANATIONS = {
    "hashmap": {
        "get": "get value for key (default if missing) — O(1) avg",
        "pop": "remove key & return its value — O(1) avg",
        "setdefault": "get value, inserting a default if key is missing — O(1) avg",
        "update": "merge another mapping in — O(k)",
        "keys": "get a view of all keys — O(1) to create, O(n) to scan",
        "values": "get a view of all values — O(1) to create, O(n) to scan",
        "items": "get a view of all (key, value) pairs — O(1) to create, O(n) to scan",
        "clear": "remove all entries — O(n)",
        "copy": "shallow-copy the map — O(n)",
    },
    "set": {
        "add": "add an element — O(1) avg",
        "discard": "remove element if present (no error) — O(1) avg",
        "remove": "remove element (raises if missing) — O(1) avg",
        "pop": "remove & return an arbitrary element — O(1)",
        "union": "combine with another set — O(len(a)+len(b))",
        "intersection": "elements common to both sets — O(min(len(a),len(b)))",
        "difference": "elements only in this set — O(len(a))",
        "issubset": "check if this set is contained in another — O(len(a))",
        "clear": "remove all elements — O(n)",
    },
    "array": {
        "append": "add to the end — O(1) amortized",
        "appendleft": "add to the front (deque) — O(1)",
        "pop": "remove last item (or by index) — O(1) at end / O(n) elsewhere",
        "popleft": "remove from the front (deque) — O(1)",
        "insert": "insert at an index — O(n), shifts later elements",
        "remove": "remove first matching value — O(n) (has to search)",
        "sort": "sort in place — O(n log n)",
        "reverse": "reverse in place — O(n)",
        "index": "find the index of a value — O(n) (has to search)",
        "count": "count occurrences of a value — O(n)",
        "extend": "append all items from another iterable — O(k)",
        "clear": "remove all elements — O(n)",
        "copy": "shallow-copy the list — O(n)",
    },
}

# heapq module-level functions (operate on a plain list used as a heap)
HEAP_METHOD_EXPLANATIONS = {
    "heappush": "push a value onto the heap — O(log n)",
    "heappop": "pop the smallest value — O(log n)",
    "heapify": "rearrange a list into heap order — O(n)",
    "heappushpop": "push then pop the smallest — O(log n)",
    "heapreplace": "pop smallest then push a new value — O(log n)",
    "nlargest": "get the n largest elements — O(n log k)",
    "nsmallest": "get the n smallest elements — O(n log k)",
}

# Parameter names commonly seen in LeetCode-style signatures that (almost
# always) refer to an array/sequence, used as a fallback ONLY when there's
# no type annotation to go on.
ARRAY_PARAM_HINTS = {
    "nums", "nums1", "nums2", "arr", "array", "lst", "matrix", "grid", "board",
    "strs", "words", "points", "intervals", "edges", "heights", "prices",
    "digits", "candidates", "tokens", "buildings", "piles", "weights",
    "values", "coins", "ropes", "graph",
}


class _IdGen:
    """Shared, monotonically-increasing id generator so node ids stay
    unique even when several FlowchartBuilders draw into one combined
    image (one cluster per function). Also tracks which data-structure
    categories were actually used, so a legend can be added only when
    relevant."""
    def __init__(self):
        self.counter = 0
        self.used_categories = set()

    def new(self):
        self.counter += 1
        return f"n{self.counter}"


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

    def __init__(self, dot=None, idgen=None, name="flowchart", rankdir="TB"):
        """
        dot:   an existing graphviz Digraph (or subgraph/cluster) to draw into.
               If None, a fresh top-level Digraph is created (standalone mode).
        idgen: a shared _IdGen so node ids stay unique across multiple
               builders drawing into the same combined image. If None, a
               private counter is used (fine for standalone mode).
        """
        if dot is None:
            dot = Digraph(name=name)
            dot.attr(rankdir=rankdir, splines="polyline", nodesep="0.45", ranksep="0.55")
            dot.attr("node", fontname="Helvetica", fontsize="11")
            dot.attr("edge", fontname="Helvetica", fontsize="9")
        self.dot = dot
        self.idgen = idgen or _IdGen()

        # Stacks for handling control flow that jumps out of normal sequence
        self.loop_break_stack = []      # list of lists: collected break-frontiers per loop
        self.loop_continue_stack = []   # list of node ids: the loop's condition node
        self.func_return_stack = []     # list of node ids: the function's End node

        # Best-effort tracking of what data structure each variable holds
        # (populated from parameter type hints/names and from assignments
        # like `seen = {}` as we walk the code). Used to generate the
        # plain-English hash-map/set/array explanations.
        self.var_types = {}

    # -- helpers ---------------------------------------------------------
    def _new_id(self):
        return self.idgen.new()

    @staticmethod
    def _wrap(text, width=WRAP_WIDTH):
        """Wrap long lines to a max width, but preserve any intentional
        newlines already in `text` (e.g. between code and its explanation)
        instead of collapsing them."""
        text = str(text)
        out_lines = []
        for line in text.split("\n"):
            if len(line) > width:
                out_lines.extend(textwrap.wrap(line, width) or [line])
            else:
                out_lines.append(line)
        return "\n".join(out_lines)

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

    # -- data-structure type tracking / explanations ----------------------
    def _mark_used(self, category):
        if category:
            self.idgen.used_categories.add(category)
        return category

    def _category_color(self, category, default=COLOR_PROCESS):
        return CATEGORY_INFO.get(category, (None, default, None))[1]

    @staticmethod
    def _call_name(call):
        """Return the plain function/method name being called, e.g.
        `dict()` -> 'dict', `collections.Counter()` -> 'Counter',
        `seen.get(x)` -> 'get'."""
        f = call.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
        return None

    def _infer_expr_type(self, value):
        """Best-effort: does this expression create a hash map / set / array?"""
        if isinstance(value, (ast.Dict, ast.DictComp)):
            return "hashmap"
        if isinstance(value, (ast.Set, ast.SetComp)):
            return "set"
        if isinstance(value, (ast.List, ast.ListComp)):
            return "array"
        if isinstance(value, ast.Call):
            name = self._call_name(value)
            if name in CREATE_HASHMAP_CALLS:
                return "hashmap"
            if name in CREATE_SET_CALLS:
                return "set"
            if name in CREATE_ARRAY_CALLS:
                return "array"
        return None

    def _type_from_annotation(self, ann):
        """Infer a category from a type hint like `List[int]` or `Dict[str,int]`."""
        if ann is None:
            return None
        s = self._src(ann).lower()
        if "dict" in s or "counter" in s:
            return "hashmap"
        if "set" in s and "offset" not in s:
            return "set"
        if "list" in s or "array" in s or "deque" in s or "sequence" in s:
            return "array"
        return None

    def _method_tag(self, category, method):
        """Look up the plain-English explanation for e.g. category='array',
        method='append'. Returns a ready-to-display tag string, or None."""
        expl = METHOD_EXPLANATIONS.get(category, {}).get(method)
        if not expl:
            return None
        human_name = CATEGORY_INFO.get(category, (category,))[0]
        return f"{human_name}: {method}() — {expl}"

    def _membership_tag(self, node):
        """For `x in y` / `x not in y` conditions, explain the lookup cost
        based on what `y` is. Returns (tag_text, category) or (None, None)."""
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], (ast.In, ast.NotIn))):
            return None, None

        right = node.comparators[0]
        if isinstance(right, ast.Name):
            cat = self.var_types.get(right.id)
            if cat == "hashmap":
                return "Hash Map key lookup — O(1) avg", "hashmap"
            if cat == "set":
                return "Set membership check — O(1) avg", "set"
            if cat == "array":
                return "Linear search through array — O(n)", "array"
        elif isinstance(right, ast.Call) and isinstance(right.func, ast.Attribute):
            base = right.func.value
            if isinstance(base, ast.Name):
                base_cat = self.var_types.get(base.id)
                if base_cat == "hashmap" and right.func.attr == "values":
                    return ("Hash Map: scanning .values() is O(n) — "
                            "NOT O(1) like checking keys directly"), "hashmap"
                if base_cat == "hashmap" and right.func.attr == "keys":
                    return "Hash Map key lookup — O(1) avg", "hashmap"
        return None, None

    def _handle_assign(self, stmt):
        """Returns (label, fillcolor, category_or_None) for an Assign node."""
        src = self._src(stmt)

        # heapq.heappush/heappop/etc. on the RHS -- applies regardless of
        # whether the target is a simple name or a tuple-unpack.
        if (isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute)
                and isinstance(stmt.value.func.value, ast.Name) and stmt.value.func.value.id == "heapq"
                and stmt.value.func.attr in HEAP_METHOD_EXPLANATIONS):
            tag = f"Heap: {stmt.value.func.attr}() — {HEAP_METHOD_EXPLANATIONS[stmt.value.func.attr]}"
            return f"{src}\n{tag}", COLOR_HEAP, "heap"

        if len(stmt.targets) != 1:
            return src, COLOR_PROCESS, None
        target = stmt.targets[0]

        if isinstance(target, ast.Name):
            new_cat = self._infer_expr_type(stmt.value)
            if new_cat:
                self.var_types[target.id] = new_cat
                human_name = CATEGORY_INFO[new_cat][0]
                empty = (
                    (isinstance(stmt.value, (ast.Dict, ast.Set, ast.List)) and not (
                        getattr(stmt.value, "keys", None) or getattr(stmt.value, "elts", None)))
                    or (isinstance(stmt.value, ast.Call) and not stmt.value.args)
                )
                verb = "created (empty)" if empty else "created"
                return f"{src}\n{human_name} {verb}", self._category_color(new_cat), new_cat

            # RHS is a method call on an already-tracked variable, e.g. x = seen.get(k)
            if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute):
                base = stmt.value.func.value
                if isinstance(base, ast.Name):
                    base_cat = self.var_types.get(base.id)
                    tag = self._method_tag(base_cat, stmt.value.func.attr)
                    if tag:
                        return f"{src}\n{tag}", self._category_color(base_cat), base_cat

            # RHS reads a value out of a tracked container: x = seen[key] / x = nums[i]
            if isinstance(stmt.value, ast.Subscript) and isinstance(stmt.value.value, ast.Name):
                base_cat = self.var_types.get(stmt.value.value.id)
                if base_cat == "hashmap":
                    return f"{src}\nHash Map: read value by key — O(1) avg", self._category_color("hashmap"), "hashmap"
                if base_cat == "array":
                    return f"{src}\nArray: read by index — O(1)", self._category_color("array"), "array"

            # Reassigned to something we can't classify -- forget old tracking
            # so we don't mislabel later uses of this name.
            self.var_types.pop(target.id, None)

        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            base_cat = self.var_types.get(target.value.id)
            if base_cat == "hashmap":
                return f"{src}\nHash Map: insert/update value at key — O(1) avg", self._category_color("hashmap"), "hashmap"
            if base_cat == "array":
                return f"{src}\nArray: update element by index — O(1)", self._category_color("array"), "array"

        return src, COLOR_PROCESS, None

    def _handle_augassign(self, stmt):
        """Returns (label, fillcolor, category_or_None) for an AugAssign node."""
        src = self._src(stmt)
        target = stmt.target
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            base_cat = self.var_types.get(target.value.id)
            if base_cat == "hashmap":
                return (f"{src}\nHash Map: update value at key — O(1) avg "
                        "(classic frequency-counting pattern)"), self._category_color("hashmap"), "hashmap"
            if base_cat == "array":
                return f"{src}\nArray: update element by index — O(1)", self._category_color("array"), "array"
        return src, COLOR_PROCESS, None

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
        if isinstance(stmt, ast.Assign):
            label, color, cat = self._handle_assign(stmt)
            self._mark_used(cat)
            nid = self.add_node(label, fillcolor=color)
            self.connect_frontier(frontier, nid)
            return [(nid, None)]
        if isinstance(stmt, ast.AugAssign):
            label, color, cat = self._handle_augassign(stmt)
            self._mark_used(cat)
            nid = self.add_node(label, fillcolor=color)
            self.connect_frontier(frontier, nid)
            return [(nid, None)]
        # AnnAssign / anything else generic
        nid = self.add_node(self._src(stmt), fillcolor=COLOR_PROCESS)
        self.connect_frontier(frontier, nid)
        return [(nid, None)]

    # -- individual statement handlers -----------------------------------
    def _process_expr(self, stmt, frontier):
        value = stmt.value
        if isinstance(value, ast.Call):
            func = value.func
            # heapq.heappush(heap, x) etc.
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "heapq" and func.attr in HEAP_METHOD_EXPLANATIONS):
                tag = f"Heap: {func.attr}() — {HEAP_METHOD_EXPLANATIONS[func.attr]}"
                nid = self.add_node(f"{self._src(stmt)}\n{tag}", fillcolor=COLOR_HEAP)
                self._mark_used("heap")
                self.connect_frontier(frontier, nid)
                return [(nid, None)]
            # method call on a tracked hash map / set / array variable
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                base_cat = self.var_types.get(func.value.id)
                tag = self._method_tag(base_cat, func.attr)
                if tag:
                    nid = self.add_node(f"{self._src(stmt)}\n{tag}", fillcolor=self._category_color(base_cat))
                    self._mark_used(base_cat)
                    self.connect_frontier(frontier, nid)
                    return [(nid, None)]
            if isinstance(func, ast.Name) and func.id == "print":
                nid = self.add_node(self._src(stmt), shape="parallelogram", fillcolor=COLOR_IO)
                self.connect_frontier(frontier, nid)
                return [(nid, None)]
        nid = self.add_node(self._src(stmt), fillcolor=COLOR_PROCESS)
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
        tag, cat = self._membership_tag(stmt.test)
        label = self._src(stmt.test)
        if tag:
            label += "\n" + tag
        color = self._category_color(cat, default=COLOR_DECISION) if cat else COLOR_DECISION
        self._mark_used(cat)
        cond_id = self.add_node(label, shape="diamond", fillcolor=color)
        self.connect_frontier(frontier, cond_id)

        true_frontier = self.process_block(stmt.body, [(cond_id, "Yes")])
        if stmt.orelse:
            false_frontier = self.process_block(stmt.orelse, [(cond_id, "No")])
        else:
            false_frontier = [(cond_id, "No")]
        return true_frontier + false_frontier

    def _iteration_tag(self, stmt):
        """For `for x in <expr>`, explain what's being iterated and its cost.
        Returns (tag_text, category) or (None, None)."""
        it = stmt.iter
        base_name, method, via_enumerate = None, None, False

        if isinstance(it, ast.Name):
            base_name = it.id
        elif isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) and isinstance(it.func.value, ast.Name):
            base_name = it.func.value.id
            method = it.func.attr
        elif (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and it.func.id in ("enumerate", "reversed", "sorted", "iter") and it.args):
            inner = it.args[0]
            via_enumerate = it.func.id == "enumerate"
            if isinstance(inner, ast.Name):
                base_name = inner.id
            elif isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and isinstance(inner.func.value, ast.Name):
                base_name = inner.func.value.id
                method = inner.func.attr

        if not base_name:
            return None, None
        cat = self.var_types.get(base_name)
        if cat == "hashmap":
            if method == "items":
                text = "Hash Map: iterate (key, value) pairs — O(n)"
            elif method == "values":
                text = "Hash Map: iterate values — O(n)"
            else:
                text = "Hash Map: iterate keys — O(n)"
        elif cat == "set":
            text = "Set: iterate elements — O(n), no guaranteed order"
        elif cat == "array":
            text = "Array: iterate elements — O(n)"
        else:
            return None, None
        if via_enumerate:
            text += " (paired with index via enumerate)"
        return text, cat

    def _process_for(self, stmt, frontier):
        tag, cat = self._iteration_tag(stmt)
        label = f"for {self._src(stmt.target)} in {self._src(stmt.iter)}"
        if tag:
            label += "\n" + tag
        color = self._category_color(cat, default=COLOR_DECISION) if cat else COLOR_DECISION
        self._mark_used(cat)
        cond_id = self.add_node(label, shape="diamond", fillcolor=color)
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
        tag, cat = self._membership_tag(stmt.test)
        label = self._src(stmt.test)
        if tag:
            label += "\n" + tag
        color = self._category_color(cat, default=COLOR_DECISION) if cat else COLOR_DECISION
        self._mark_used(cat)
        cond_id = self.add_node(label, shape="diamond", fillcolor=color)
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
    """High-level entry point: turns Python source into rendered flowchart
    image file(s).

    By default everything in the file (every function, every method, and
    any top-level script code) is drawn into ONE combined image, with each
    function shown as its own labeled box ("cluster"). Pass
    combine=False to instead get one separate image per function.
    """

    def __init__(self, fmt="png", rankdir="TB", combine=True):
        self.fmt = fmt
        self.rankdir = rankdir
        self.combine = combine

    @staticmethod
    def _func_args(func_node):
        args = (
            func_node.args.posonlyargs + func_node.args.args
            if hasattr(func_node.args, "posonlyargs") else func_node.args.args
        )
        return ", ".join(a.arg for a in args)

    def _draw_function(self, dot, idgen, func_node, display_name=None):
        name = display_name or func_node.name
        builder = FlowchartBuilder(dot=dot, idgen=idgen, name=name)

        # Seed variable-type tracking from parameter type hints, falling
        # back to common LeetCode parameter-name conventions (e.g. `nums`)
        # when there's no annotation to go on.
        args = (
            func_node.args.posonlyargs + func_node.args.args
            if hasattr(func_node.args, "posonlyargs") else func_node.args.args
        )
        for a in args:
            cat = builder._type_from_annotation(a.annotation) if a.annotation is not None else None
            if cat is None and a.arg.lower() in ARRAY_PARAM_HINTS:
                cat = "array"
            if cat:
                builder.var_types[a.arg] = cat

        args_str = self._func_args(func_node)
        start_id = builder.add_node(f"Start\n{func_node.name}({args_str})", shape="oval", fillcolor=COLOR_START_END)
        end_id = builder.add_node(f"End\n{func_node.name}", shape="oval", fillcolor=COLOR_END)

        builder.func_return_stack.append(end_id)
        frontier = builder.process_block(func_node.body, [(start_id, None)])
        builder.connect_frontier(frontier, end_id)
        builder.func_return_stack.pop()

    def _draw_script(self, dot, idgen, stmts, name="main"):
        builder = FlowchartBuilder(dot=dot, idgen=idgen, name=name)
        start_id = builder.add_node("Start", shape="oval", fillcolor=COLOR_START_END)
        end_id = builder.add_node("End", shape="oval", fillcolor=COLOR_END)
        frontier = builder.process_block(stmts, [(start_id, None)])
        builder.connect_frontier(frontier, end_id)

    @staticmethod
    def _add_legend(dot, idgen):
        """Add a small reference box explaining the data-structure colors,
        but only for the categories actually used in this chart."""
        used = [c for c in ("hashmap", "set", "array", "heap") if c in idgen.used_categories]
        if not used:
            return
        with dot.subgraph(name="cluster_legend") as c:
            c.attr(label="Legend", fontsize="12", style="dashed", color="#BDBDBD", labelloc="t", margin="14")
            for cat in used:
                human_name, color, desc = CATEGORY_INFO[cat]
                nid = idgen.new()
                c.node(nid, label=desc, shape="box", style="filled,rounded", fillcolor=color, fontsize="10")

    def _collect_targets(self, tree, only_function):
        """Returns list of (cluster_name, display_label, func_node) plus leftover top-level stmts."""
        targets = []
        leftover_stmts = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if only_function and node.name != only_function:
                    continue
                targets.append((node.name, node.name, node))
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if only_function and item.name != only_function:
                            continue
                        targets.append((f"{node.name}_{item.name}", f"{node.name}.{item.name}", item))
            else:
                leftover_stmts.append(node)
        return targets, leftover_stmts

    def convert_source(self, code, output_prefix="flowchart", only_function=None, outdir="."):
        """
        Parse `code` and render flowchart image(s).
        Returns a list of the rendered file path(s) (just one, if combine=True).
        """
        os.makedirs(outdir, exist_ok=True)
        tree = ast.parse(code)
        targets, leftover_stmts = self._collect_targets(tree, only_function)

        if not targets and not (leftover_stmts and not only_function):
            raise ValueError(
                "Nothing to render -- no functions/classes found "
                "(and no top-level statements outside of them)."
            )

        if self.combine:
            dot = Digraph(name=output_prefix, format=self.fmt)
            dot.attr(rankdir=self.rankdir, splines="polyline", nodesep="0.45", ranksep="0.55", compound="true")
            dot.attr("node", fontname="Helvetica", fontsize="11")
            dot.attr("edge", fontname="Helvetica", fontsize="9")
            idgen = _IdGen()

            for cluster_key, label, func_node in targets:
                with dot.subgraph(name=f"cluster_{cluster_key}") as c:
                    c.attr(label=label, fontsize="14", style="rounded,filled", fillcolor="#FAFAFA",
                           color="#9E9E9E", labelloc="t", margin="16")
                    self._draw_function(c, idgen, func_node,
                                         display_name=label if "." in label else None)

            if leftover_stmts and not only_function:
                with dot.subgraph(name="cluster_main") as c:
                    c.attr(label="main (top-level script code)", fontsize="14", style="rounded,filled",
                           fillcolor="#FAFAFA", color="#9E9E9E", labelloc="t", margin="16")
                    self._draw_script(c, idgen, leftover_stmts)

            self._add_legend(dot, idgen)
            path = dot.render(filename=output_prefix, directory=outdir, cleanup=True)
            return [path]

        # -- non-combined: one file per function (old behaviour) --
        outputs = []
        for cluster_key, label, func_node in targets:
            dot = Digraph(name=label, format=self.fmt)
            dot.attr(rankdir=self.rankdir, splines="polyline", nodesep="0.45", ranksep="0.55")
            dot.attr("node", fontname="Helvetica", fontsize="11")
            dot.attr("edge", fontname="Helvetica", fontsize="9")
            idgen = _IdGen()
            self._draw_function(dot, idgen, func_node, display_name=label if "." in label else None)
            self._add_legend(dot, idgen)
            path = dot.render(filename=f"{output_prefix}_{cluster_key}", directory=outdir, cleanup=True)
            outputs.append(path)

        if leftover_stmts and not only_function:
            dot = Digraph(name="main", format=self.fmt)
            dot.attr(rankdir=self.rankdir, splines="polyline", nodesep="0.45", ranksep="0.55")
            dot.attr("node", fontname="Helvetica", fontsize="11")
            dot.attr("edge", fontname="Helvetica", fontsize="9")
            idgen = _IdGen()
            self._draw_script(dot, idgen, leftover_stmts)
            self._add_legend(dot, idgen)
            path = dot.render(filename=f"{output_prefix}_main", directory=outdir, cleanup=True)
            outputs.append(path)

        return outputs


def main():
    parser = argparse.ArgumentParser(description="Convert Python code into flowchart images.")
    parser.add_argument("input", help="Path to a .py file to convert")
    parser.add_argument("-o", "--output", default="flowchart", help="Output filename (without extension)")
    parser.add_argument("--outdir", default="flowcharts", help="Output directory (created if missing)")
    parser.add_argument("--format", default="png", choices=["png", "svg", "pdf"], help="Image format")
    parser.add_argument("--function", default=None, help="Only render this one function/method by name")
    parser.add_argument("--rankdir", default="TB", choices=["TB", "LR"], help="Flowchart direction")
    parser.add_argument("--separate", action="store_true",
                         help="Render one image per function instead of combining everything into one")
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

    converter = PythonFlowchartConverter(fmt=args.format, rankdir=args.rankdir, combine=not args.separate)
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