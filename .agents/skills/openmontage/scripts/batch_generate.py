#!/usr/bin/env python3
"""Generate first frames or clips from a shot spec, with cost tracking and a concurrency cap.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/batch_generate.py --project <id> --spec shots.json --stage frames --dry-run
  python -X utf8 <skill>/scripts/batch_generate.py --project <id> --spec shots.json --stage videos --only a1 a2

Spec: {"budget_usd": 4.0, "resolution": "768P", "shots": [{"id": "a1", "refs": ["assets/images/source/x.png"],
"frame_prompt": "...", "duration": 6, "video_prompt": "..."}]}
Refs are relative to the project folder. A shot with no refs uses text-to-image.
Existing outputs are skipped. --resubmit is a new paid attempt and needs user approval.
"""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from tools.cost_tracker import CostTracker  # noqa: E402
from tools.tool_registry import registry  # noqa: E402

# Fallbacks only; the live defaults come from routing.default_tools in the relay config,
# so switching models (python -m tools.relay route ...) needs no script change.
FALLBACK_IMAGE_TOOL = "relay_tststs_gpt_image_2_6"
FALLBACK_VIDEO_TOOL = "relay_omniakey_minimax_h3"
RATES = {"768P": 0.0269, "2K": 0.0431}


def default_tool(kind, fallback):
    try:
        from tools.relay.config import try_load_relay_config

        return try_load_relay_config().routing.default_tools.get(kind) or fallback
    except Exception:
        return fallback


def size_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return tuple(int(x) for x in out.split(",")[:2]) if out else (0, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--stage", choices=("frames", "videos"), required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resubmit", action="store_true", help="new paid attempt; only after user approval")
    ap.add_argument("--image-tool", default=default_tool("image", FALLBACK_IMAGE_TOOL))
    ap.add_argument("--video-tool", default=default_tool("video", FALLBACK_VIDEO_TOOL))
    ap.add_argument("--workers", type=int)
    args = ap.parse_args()

    proj = Path("projects") / args.project
    if not proj.exists():
        sys.exit(f"Project not found from {Path.cwd()}: {proj}")
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    resolution = spec.get("resolution", "768P")
    if resolution not in RATES:
        sys.exit(f"Unknown resolution {resolution}")
    shots = [s for s in spec["shots"] if not args.only or s["id"] in args.only]
    images, videos = proj / "assets/images", proj / "assets/video"
    images.mkdir(parents=True, exist_ok=True)
    videos.mkdir(parents=True, exist_ok=True)

    jobs = {}
    for s in shots:
        sid = s["id"]
        if args.stage == "frames":
            out = images / f"{sid}.png"
            refs = [str(proj / r) for r in s.get("refs", [])]
            missing = [r for r in refs if not Path(r).exists()]
            if missing:
                sys.exit(f"{sid}: missing refs {missing}")
            inputs = {"preferred_tool": args.image_tool, "generation_mode": "edit" if refs else "generate", "aspect_ratio": spec.get("aspect_ratio", "9:16"),
                      "output_path": str(out), "scene_id": sid, "prompt": s["frame_prompt"]}
            if refs:
                inputs["image_paths"] = refs
            registry.ensure_discovered()
            image_tool = registry.get(args.image_tool)
            if image_tool is None:
                sys.exit(f"image tool {args.image_tool} is not registered; run python -m tools.relay list")
            cost = image_tool.estimate_cost(inputs)  # price from the relay config
        else:
            out = videos / f"{sid}.mp4"
            frame = images / f"{sid}.png"
            if not frame.exists():
                sys.exit(f"{sid}: first frame missing: {frame}")
            inputs = {"preferred_tool": args.video_tool, "operation": "image_to_video", "reference_image_path": str(frame), "duration": s["duration"],
                      "resolution": resolution, "aspect_ratio": spec.get("aspect_ratio", "9:16"), "output_path": str(out), "scene_id": sid, "prompt": s["video_prompt"]}
            cost = round(s["duration"] * RATES[resolution], 4)
        if out.exists() and not args.resubmit and not args.dry_run:
            print(f"{sid}: already exists, skipped")
            continue
        if args.resubmit:
            inputs["confirm_resubmit"] = True
        jobs[sid] = (inputs, cost)

    total = round(sum(c for _, c in jobs.values()), 4)
    print(f"{args.stage}: {len(jobs)} job(s), estimated ${total}")
    if not jobs:
        return
    registry.discover()
    selector = registry.get("image_selector" if args.stage == "frames" else "video_selector")

    if args.dry_run:
        for sid, (inputs, _) in jobs.items():
            probe = dict(inputs, dry_run=True)
            if args.stage == "videos" and not Path(probe["reference_image_path"]).exists():
                continue
            r = selector.execute(probe)
            print(sid, "dry_run", r.success, r.error or "")
        return

    tool = args.image_tool if args.stage == "frames" else args.video_tool
    tracker = CostTracker(budget_total_usd=spec.get("budget_usd", 4.0), cost_log_path=proj / "cost_log.json")
    tracker.approve_tool(tool)
    entries = {}
    for sid, (_, cost) in jobs.items():
        entry = tracker.estimate(tool, sid, cost)
        tracker.reserve(entry)
        entries[sid] = entry

    def run(item):
        sid, (inputs, _) = item
        try:
            r = selector.execute(inputs)
            return sid, r.success, r.error, r.cost_usd, (r.data or {}).get("status")
        except Exception as exc:
            return sid, False, repr(exc)[:300], 0, None

    workers = args.workers or (6 if args.stage == "frames" else 3)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(run, jobs.items()))

    failed = 0
    for sid, ok, err, cost, status in rows:
        if ok:
            tracker.reconcile(entries[sid], cost or jobs[sid][1], True)
        else:
            tracker.refund(entries[sid])
            failed += 1
        print(sid, "ok" if ok else "FAILED", status or "", str(err or "")[:200])
    if args.stage == "frames":
        for sid in jobs:
            w, h = size_of(images / f"{sid}.png")
            if h and abs(w / h - 9 / 16) > 0.01 and spec.get("aspect_ratio", "9:16") == "9:16":
                print(f"{sid}: returned {w}x{h}, not 9:16. Keep the original and ask before cropping.")
    print(tracker.cost_snapshot())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
