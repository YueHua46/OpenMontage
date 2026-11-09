#!/usr/bin/env python3
"""Turn a reviewed project into a reusable playbook, and start a new project from one. No paid calls.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/playbook.py save  --project <done-id> --spec shots.json --name <name>
  python -X utf8 <skill>/scripts/playbook.py check --playbook playbooks/<name>.json
  python -X utf8 <skill>/scripts/playbook.py apply --playbook playbooks/<name>.json --project <new-id> --spec new_shots.json

A playbook keeps the method, not a fixed prompt:
  model     tool, resolution, aspect ratio and extra inputs that produced accepted takes
  method    how this kind of video is shot (style, rhythm, structure, product rules), written by the agent
  checks    acceptance rules for every shot
  shots     one entry per shot purpose: accepted prompts as worked examples, checks, failed attempts as lessons
Only takes marked pass in review.json become worked examples. Failed or unreviewed shots are kept as
"unresolved" with their lessons, never as proven.

`save` writes a draft with TODO fields; the agent fills them from the review notes, the user approves,
then `check` marks it ready. `apply` copies model settings and checks into a new spec the agent has
written for the new brief, and prints the worked example for each shot purpose.
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_shots import Review, dump, load, norm_checks  # noqa: E402

TODO = "TODO"
MODEL_KEYS = ("video_tool", "image_tool", "resolution", "aspect_ratio", "video_inputs", "max_retakes")


def sha(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else None


def image_job(proj, shot):
    from batch_generate import frame_file

    job = load(Path(str(frame_file(proj, shot)) + ".relay-job.json"), {}) or {}
    return (job.get("request_summary") or {}).get("prompt"), job.get("tool")


def todos(node, where=""):
    if isinstance(node, str):
        return [where] if node.startswith(TODO) else []
    if isinstance(node, dict):
        return [t for k, v in node.items() for t in todos(v, f"{where}.{k}" if where else k)]
    if isinstance(node, list):
        return [t for i, v in enumerate(node) for t in todos(v, f"{where}[{i}]")]
    return []


def cmd_save(args):
    r = Review(args.project, args.spec)
    spec, proj = r.spec, r.proj
    tools = {t.get("tool") for rec in r.data["shots"].values() for t in rec["takes"].values() if t.get("verdict") == "pass"}
    model = {k: spec[k] for k in MODEL_KEYS if k in spec}
    if len(tools - {None}) == 1:
        model["video_tool"] = next(iter(tools - {None}))
    shots, counts = [], {"proven": 0, "unresolved": 0, "unreviewed": 0}
    for s in spec["shots"]:
        rec = r.data["shots"][s["id"]]
        attempts = [{"take": int(n), "clip": t.get("clip"), "verdict": t["verdict"], "notes": t.get("notes", ""),
                     "failed_checks": t.get("failed_checks", [])} for n, t in sorted(rec["takes"].items(), key=lambda x: int(x[0]))]
        passed = next((t for t in rec["takes"].values() if t.get("clip") == rec.get("selected")), None)
        status = "proven" if passed else ("unresolved" if attempts and any(a["verdict"] == "fail" for a in attempts) else "unreviewed")
        counts[status] += 1
        entry = {"id": s["id"], "purpose": s.get("purpose") or f"{TODO}: what this shot does for the video",
                 "duration": s.get("duration"), "status": status, "checks": norm_checks(s.get("checks")),
                 "attempts": attempts, "retakes_used": max(0, len(attempts) - 1)}
        if passed:
            frame_prompt, image_tool = image_job(proj, s)
            entry["example"] = {"clip": passed["clip"], "video_prompt": passed.get("prompt"), "frame_prompt": frame_prompt or s.get("frame_prompt"),
                                "image_tool": image_tool, "dialogue": s.get("dialogue"),
                                "refs": [{"path": p, "sha256": sha(p if Path(p).is_absolute() else proj / p)} for p in s.get("refs", [])]}
        failed_notes = [a["notes"] for a in attempts if a["verdict"] == "fail" and a["notes"]]
        entry["lessons"] = failed_notes or ([f"{TODO}: why it still fails and what was tried"] if status == "unresolved" else [])
        shots.append(entry)
    book = {"name": args.name, "status": "draft", "source_project": args.project,
            "created": dt.date.today().isoformat(), "model": model,
            "method": spec.get("method") or {"style": f"{TODO}: shooting style, pacing, voice and audio rules",
                                             "structure": f"{TODO}: shot order and what each part sells",
                                             "product_rules": f"{TODO}: product facts and geometry that must not change",
                                             "prompt_pattern": f"{TODO}: how the accepted prompts are built (common block, action, dialogue, negatives)"},
            "checks": norm_checks(spec.get("checks")), "shots": shots,
            "summary": {**counts, "retakes": sum(e["retakes_used"] for e in shots)}}
    out = Path(args.out or f"playbooks/{args.name}.json")
    if out.exists() and not args.force:
        sys.exit(f"{out} exists; pass --force to overwrite")
    dump(out, book)
    print(f"draft playbook: {out}")
    print(json.dumps(book["summary"], ensure_ascii=False))
    if counts["unresolved"] or counts["unreviewed"]:
        print("Not proven:", [e["id"] for e in shots if e["status"] != "proven"], "- these are lessons, not examples.")
    missing = todos(book)
    if missing:
        print("Fill before `check`:", *missing, sep="\n  ")


def cmd_check(args):
    path = Path(args.playbook)
    book = load(path) or sys.exit(f"no playbook {path}")
    missing = todos(book)
    if missing:
        sys.exit("Still TODO:\n  " + "\n  ".join(missing))
    if not book.get("checks") and not any(s.get("checks") for s in book["shots"]):
        sys.exit("No acceptance checks. Add the rules a reviewer must verify before marking it ready.")
    book["status"] = "ready"
    dump(path, book)
    print(f"{path}: ready ({book['summary']})")


def cmd_apply(args):
    book = load(args.playbook) or sys.exit(f"no playbook {args.playbook}")
    if book.get("status") != "ready":
        sys.exit("Playbook is not ready; run `check` after the user approves it.")
    proj = Path("projects") / args.project
    if not proj.exists():
        from lib.checkpoint import init_project

        init_project(args.project, title=args.project, pipeline_type="cinematic")
    spec_path = Path(args.spec)
    spec = load(spec_path) or sys.exit(f"Write the new shot spec first: {spec_path}")
    for k, v in book["model"].items():
        spec.setdefault(k, v)
    have = {c["id"] for c in norm_checks(spec.get("checks"))}
    spec["checks"] = norm_checks(spec.get("checks")) + [c for c in book["checks"] if c["id"] not in have]
    spec["playbook"] = book["name"]
    by_purpose = {s["purpose"]: s for s in book["shots"]}
    for s in spec["shots"]:
        ref = by_purpose.get(s.get("purpose"))
        print(f"\n== {s['id']} {s.get('purpose') or '(no purpose)'}")
        if not ref:
            print("  new shot type: no worked example, review it closely")
            continue
        own = {c["id"] for c in norm_checks(s.get("checks"))}
        s["checks"] = norm_checks(s.get("checks")) + [c for c in ref["checks"] if c["id"] not in own]
        if ref["status"] == "proven":
            print("  worked example prompt:\n   ", (ref["example"].get("video_prompt") or "")[:600].replace("\n", "\n    "))
        else:
            print(f"  UNRESOLVED in the source project ({ref['retakes_used']} retakes). Lessons:")
        for lesson in ref.get("lessons", []):
            print("   -", lesson)
    dump(spec_path, spec)
    print(f"\n{spec_path}: model settings and checks added. Next: batch_generate.py --dry-run, then the review loop.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("save")
    p.add_argument("--project", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--out")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--playbook", required=True)
    p = sub.add_parser("apply")
    p.add_argument("--playbook", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--spec", required=True)
    args = ap.parse_args()
    {"save": cmd_save, "check": cmd_check, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
