#!/usr/bin/env python3
"""Check and render a project's HyperFrames workspace.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/render.py --project <id> [--check-only] [--output final.mp4]
This needs to run outside the Codex sandbox, because the renderer starts a browser.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from tools.tool_registry import registry  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--workspace", default="hyperframes")
    ap.add_argument("--output", default="final.mp4")
    ap.add_argument("--quality", default="high", choices=("draft", "standard", "high"))
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    proj = Path("projects") / args.project
    ws = str(proj / args.workspace)
    registry.discover()
    tool = registry.get("hyperframes_compose")

    r = tool.execute({"operation": "check", "workspace_path": ws})
    report = (r.data or {}).get("report") or {}
    errors = (report.get("lint") or {}).get("errorCount")
    print(json.dumps({"check_ok": r.success, "lint_errors": errors, "error": r.error}, ensure_ascii=False))
    if not r.success:
        print("If this ran inside the Codex sandbox, run it outside the sandbox.")
        sys.exit(1)
    if args.check_only:
        return
    out = proj / "renders" / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    r = tool.execute({"operation": "render_existing", "workspace_path": ws, "output_path": str(out), "quality": args.quality, "fps": 30})
    print(json.dumps({"render_ok": r.success, "output": str(out), "error": r.error}, ensure_ascii=False))
    sys.exit(0 if r.success else 1)


if __name__ == "__main__":
    main()
