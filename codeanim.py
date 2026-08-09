#!/usr/bin/env python3
"""
codeanim.py
===========
One-command wrapper: trace a target script and render it as a Manim
animation.

    python3 codeanim.py target.py                 # low quality preview
    python3 codeanim.py target.py --quality h      # 1080p
    python3 codeanim.py target.py --quality k      # 4K
    python3 codeanim.py target.py --max-steps 200 --max-elements 12
    python3 codeanim.py target.py --no-render      # just write trace.json

Output video lands in media/videos/render/<quality>/CodeAnimation.mp4
(standard Manim output layout), and codeanim.py prints the exact path.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

QUALITY_FLAGS = {
    "l": "-ql",   # 480p15  -- fast preview
    "m": "-qm",   # 720p30
    "h": "-qh",   # 1080p60
    "k": "-qk",   # 2160p60
}


def main():
    p = argparse.ArgumentParser(description="Animate a Python script's execution with Manim.")
    p.add_argument("target", help="Path to the .py file to animate (e.g. target.py)")
    p.add_argument("--max-steps", type=int, default=400, help="Max executed lines to record")
    p.add_argument("--max-elements", type=int, default=10, help="Max array cells before using '...'")
    p.add_argument("--quality", choices=list(QUALITY_FLAGS), default="l",
                   help="l=480p (fast), m=720p, h=1080p, k=4K")
    p.add_argument("--no-render", action="store_true", help="Only produce trace.json, skip manim")
    p.add_argument("--preview", action="store_true", help="Open the video/player when done (-p flag)")
    args = p.parse_args()

    target_abs = os.path.abspath(args.target)
    if not os.path.isfile(target_abs):
        print(f"Error: file not found: {args.target}")
        sys.exit(1)

    trace_path = os.path.join(HERE, "trace.json")

    tracer_cmd = [
        sys.executable, os.path.join(HERE, "tracer.py"),
        target_abs, "-o", trace_path,
        "--max-steps", str(args.max_steps),
        "--max-elements", str(args.max_elements),
    ]
    print("$ " + " ".join(tracer_cmd))
    subprocess.run(tracer_cmd, check=True)

    if args.no_render:
        return

    if shutil.which("manim") is None:
        print("\nmanim isn't installed / on PATH. Install it and its system deps, then run:")
        print(f"  manim {QUALITY_FLAGS[args.quality]} {os.path.join(HERE, 'render.py')} CodeAnimation")
        sys.exit(1)

    render_cmd = ["manim", QUALITY_FLAGS[args.quality]]
    if args.preview:
        render_cmd.append("-p")
    render_cmd += [os.path.join(HERE, "render.py"), "CodeAnimation"]
    print("$ " + " ".join(render_cmd))
    subprocess.run(render_cmd, check=True, cwd=HERE)


if __name__ == "__main__":
    main()
