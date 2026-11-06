#!/usr/bin/env python3
"""Expand one master prompt into several video versions. Makes no paid calls.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/plan_variants.py --master master.json            # preview only
  python -X utf8 <skill>/scripts/plan_variants.py --master master.json --create   # after the user approves the table

Master file:
{
  "name": "farm", "resolution": "768P", "budget_per_variant_usd": 4.0, "vary": ["scene", "lead", "line_1"],
  "fixed": {"product": "white solar PTZ camera ..."},
  "shots": [{"id": "a1", "refs": ["{{lead_ref}}", "C:/assets/install.png"], "frame_prompt": "{{product}} in {{scene}} with {{lead}}",
             "duration": 6, "video_prompt": "{{lead}} says: {{line_1}}", "key": true}],
  "variants": [{"id": "v01", "scene": "...", "lead": "...", "lead_ref": "C:/assets/a.png", "line_1": "..."}]
}
"key": true marks the shots used for the first review. Refs should be absolute paths.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

RATES = {"768P": 0.0269, "2K": 0.0431}
IMAGE_RATE = 0.08


def fill(value, data):
    if isinstance(value, str):
        return re.sub(r"\{\{(\w+)\}\}", lambda m: str(data.get(m[1], m[0])), value)
    if isinstance(value, list):
        return [fill(v, data) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, data) for k, v in value.items()}
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--create", action="store_true", help="create the project folders and shot specs")
    args = ap.parse_args()
    master = json.loads(Path(args.master).read_text(encoding="utf-8"))
    res = master.get("resolution", "768P")
    if res not in RATES:
        sys.exit(f"Unknown resolution {res}")
    vary = master.get("vary", [])
    variants = master["variants"]

    ids = [v["id"] for v in variants]
    problems = [f"duplicate id {i}" for i in set(ids) if ids.count(i) > 1]
    warnings = []
    for field in vary:
        seen = {}
        for v in variants:
            if field not in v:
                problems.append(f"{v['id']} has no {field}")
            seen.setdefault(str(v.get(field)), []).append(v["id"])
        warnings += [f"{field} is the same in {', '.join(group)}" for group in seen.values() if len(group) > 1]

    specs, rows = {}, []
    for v in variants:
        data = {**master.get("fixed", {}), **v}
        shots = fill(master["shots"], data)
        left = sorted(set(re.findall(r"\{\{(\w+)\}\}", json.dumps(shots, ensure_ascii=False))))
        if left:
            problems.append(f"{v['id']} has unfilled fields: {', '.join(left)}")
        seconds = sum(s["duration"] for s in shots)
        key = [s for s in shots if s.get("key")]
        full = round(len(shots) * IMAGE_RATE + seconds * RATES[res], 2)
        review = round(len(key) * IMAGE_RATE, 2)
        if full > master.get("budget_per_variant_usd", 4.0):
            problems.append(f"{v['id']} costs ${full}, over the per-version budget")
        specs[v["id"]] = {"budget_usd": master.get("budget_per_variant_usd", 4.0), "resolution": res, "aspect_ratio": master.get("aspect_ratio", "9:16"), "shots": shots}
        rows.append({"id": v["id"], **{f: str(v.get(f, ""))[:40] for f in vary}, "shots": len(shots), "key_frames": len(key), "key_frame_usd": review, "full_usd": full})

    print(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"Key frames for all versions: ${round(sum(r['key_frame_usd'] for r in rows), 2)}; all versions in full: ${round(sum(r['full_usd'] for r in rows), 2)} (estimate, no retakes)")
    for w in warnings:
        print("WARNING:", w)
    if problems:
        print("\n".join("PROBLEM: " + p for p in problems))
        sys.exit(1)
    if not args.create:
        print("Preview only. Nothing was created.")
        return

    from lib.checkpoint import init_project
    for vid, spec in specs.items():
        pid = f"{master['name']}-{vid}"
        proj = Path("projects") / pid
        if not proj.exists():
            init_project(pid, title=f"{master['name']} {vid}", pipeline_type="cinematic")
        (proj / "shots.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        print("ready", proj / "shots.json")


if __name__ == "__main__":
    main()
