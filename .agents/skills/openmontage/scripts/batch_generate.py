#!/usr/bin/env python3
"""Generate first frames or clips from a shot spec, with cost tracking and a concurrency cap.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/batch_generate.py --project <id> --spec shots.json --stage frames --dry-run
  python -X utf8 <skill>/scripts/batch_generate.py --project <id> --spec shots.json --stage videos --only a1 a2

Spec (paths relative to the project folder unless absolute):
{"budget_usd": 4.0, "resolution": "720p", "aspect_ratio": "9:16", "video_tool": "...", "image_tool": "...",
 "video_inputs": {"extra_params": {...}},
 "shots": [{"id": "a1", "refs": ["assets/images/source/x.png"], "frame_prompt": "...", "duration": 6,
            "video_prompt": "...", "take": 1, "frame_take": 1, "video_inputs": {"last_frame_path": "same"}}]}

- A shot with no refs uses text-to-image.
- take N > 1 writes assets/video/<id>-takeN.mp4 (frame_take likewise for frames), so earlier takes are kept.
  review_shots.py retake bumps these numbers; do not overwrite an earlier take.
- video_inputs (spec level, then shot level) is merged into the video tool inputs, e.g. extra_params,
  last_frame_path ("same" = the shot's first frame), reference_image_paths, operation.
- Resolution, aspect ratio and price come from the tool's relay config, so any configured model works.
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


def default_tool(kind, fallback):
    try:
        from tools.relay.config import try_load_relay_config

        return try_load_relay_config().routing.default_tools.get(kind) or fallback
    except Exception:
        return fallback


def take_name(sid, take):
    take = int(take or 1)
    return sid if take == 1 else f"{sid}-take{take}"


def frame_file(proj, shot):
    return proj / "assets/images" / f"{take_name(shot['id'], shot.get('frame_take'))}.png"


def video_file(proj, shot):
    return proj / "assets/video" / f"{take_name(shot['id'], shot.get('take'))}.mp4"


def size_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return tuple(int(x) for x in out.split(",")[:2]) if out else (0, 0)


def get_tool(name):
    registry.ensure_discovered()
    tool = registry.get(name)
    if tool is None:
        sys.exit(f"tool {name} is not registered; run python -m tools.relay list")
    return tool


def check_limits(tool, resolution, aspect_ratio):
    """Refuse values the configured model does not list, instead of failing after submit."""
    limits = getattr(getattr(tool, "relay_model", None), "limits", {}) or {}
    allowed = [str(r).lower() for r in limits.get("resolutions", [])]
    if resolution and allowed and str(resolution).lower() not in allowed:
        sys.exit(f"{tool.name} does not list resolution {resolution}; configured: {limits['resolutions']}")
    ratios = [str(r) for r in limits.get("aspect_ratios", [])]
    if aspect_ratio and ratios and aspect_ratio not in ratios:
        sys.exit(f"{tool.name} does not list aspect ratio {aspect_ratio}; configured: {ratios}")


def resolve(proj, value):
    return value if Path(value).is_absolute() else str(proj / value)


def video_extra(proj, spec, shot, frame):
    merged = {**spec.get("video_inputs", {}), **shot.get("video_inputs", {})}
    for key, value in list(merged.items()):
        if key.endswith("_path") and isinstance(value, str):
            merged[key] = str(frame) if value == "same" else resolve(proj, value)
        elif key.endswith("_paths") and isinstance(value, list):
            merged[key] = [str(frame) if v == "same" else resolve(proj, v) for v in value]
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--stage", choices=("frames", "videos"), required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resubmit", action="store_true", help="new paid attempt; only after user approval")
    ap.add_argument("--image-tool")
    ap.add_argument("--video-tool")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--skip-frame-review", action="store_true", help="generate videos from unreviewed frames; only with user approval")
    args = ap.parse_args()

    proj = Path("projects") / args.project
    if not proj.exists():
        sys.exit(f"Project not found from {Path.cwd()}: {proj}")
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    image_tool_name = args.image_tool or spec.get("image_tool") or default_tool("image", FALLBACK_IMAGE_TOOL)
    video_tool_name = args.video_tool or spec.get("video_tool") or default_tool("video", FALLBACK_VIDEO_TOOL)
    aspect = spec.get("aspect_ratio", "9:16")
    resolution = spec.get("resolution")
    shots = [s for s in spec["shots"] if not args.only or s["id"] in args.only]
    (proj / "assets/images").mkdir(parents=True, exist_ok=True)
    (proj / "assets/video").mkdir(parents=True, exist_ok=True)

    tool_name = image_tool_name if args.stage == "frames" else video_tool_name
    tool = get_tool(tool_name)
    if args.stage == "videos":
        check_limits(tool, resolution, aspect)

    # First-frame gate: when the spec has acceptance checks, a video is only made from a frame that
    # review_shots.py marked pass. Frames are cheap; most defects (phone reversed, extra device) start there.
    review = {}
    if args.stage == "videos" and spec.get("checks") and not args.skip_frame_review:
        review = (json.loads((proj / "review.json").read_text(encoding="utf-8")) if (proj / "review.json").exists() else {}).get("shots", {})

    jobs = {}
    for s in shots:
        sid = s["id"]
        frame = frame_file(proj, s)
        if args.stage == "frames":
            out = frame
            refs = [resolve(proj, r) for r in s.get("refs", [])]
            missing = [r for r in refs if not Path(r).exists()]
            if missing:
                sys.exit(f"{sid}: missing refs {missing}")
            inputs = {"preferred_tool": tool_name, "generation_mode": "edit" if refs else "generate", "aspect_ratio": aspect,
                      "output_path": str(out), "scene_id": sid, "prompt": s["frame_prompt"]}
            if refs:
                inputs["image_paths"] = refs
        else:
            out = video_file(proj, s)
            if not frame.exists() and not args.dry_run:
                sys.exit(f"{sid}: first frame missing: {frame}")
            if spec.get("checks") and not args.skip_frame_review and not args.dry_run and not out.exists():
                verdict = review.get(sid, {}).get("frames", {}).get(str(s.get("frame_take", 1)), {}).get("verdict")
                if verdict != "pass":
                    print(f"{sid}: first frame {frame.name} is {verdict or 'not reviewed'}; review it (review_shots.py sheet/mark --stage frames) first. Skipped.")
                    continue
            inputs = {"preferred_tool": tool_name, "operation": "image_to_video", "reference_image_path": str(frame), "duration": s["duration"],
                      "aspect_ratio": aspect, "output_path": str(out), "scene_id": sid, "prompt": s["video_prompt"]}
            if resolution:
                inputs["resolution"] = resolution
            inputs.update(video_extra(proj, spec, s, frame))
        if out.exists() and not args.resubmit and not args.dry_run:
            print(f"{out.stem}: already exists, skipped")
            continue
        if args.resubmit:
            inputs["confirm_resubmit"] = True
        try:
            cost = round(tool.estimate_cost(inputs), 4)  # price from the relay config
        except Exception as exc:
            sys.exit(f"{sid}: no price for {tool_name}: {exc}")
        jobs[out.stem] = (inputs, cost)

    total = round(sum(c for _, c in jobs.values()), 4)
    print(f"{args.stage} with {tool_name}: {len(jobs)} job(s), estimated ${total}")
    if not jobs:
        return
    selector = registry.get("image_selector" if args.stage == "frames" else "video_selector")

    if args.dry_run:
        for key, (inputs, cost) in jobs.items():
            if args.stage == "videos" and not Path(inputs["reference_image_path"]).exists():
                print(key, "dry_run skipped: first frame not generated yet", f"${cost}")
                continue
            r = selector.execute(dict(inputs, dry_run=True))
            print(key, "dry_run", r.success, f"${cost}", r.error or "")
        return

    tracker = CostTracker(budget_total_usd=spec.get("budget_usd", 4.0), cost_log_path=proj / "cost_log.json")
    tracker.approve_tool(tool_name)
    entries = {}
    for key, (_, cost) in jobs.items():
        entry = tracker.estimate(tool_name, key, cost)
        tracker.reserve(entry)
        entries[key] = entry

    def run(item):
        key, (inputs, _) = item
        try:
            r = selector.execute(inputs)
            return key, r.success, r.error, r.cost_usd, (r.data or {}).get("status")
        except Exception as exc:
            return key, False, repr(exc)[:300], 0, None

    workers = args.workers or (6 if args.stage == "frames" else 3)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(run, jobs.items()))

    failed = 0
    for key, ok, err, cost, status in rows:
        if ok:
            tracker.reconcile(entries[key], cost or jobs[key][1], True)
        else:
            tracker.refund(entries[key])
            failed += 1
        print(key, "ok" if ok else "FAILED", status or "", str(err or "")[:200])
    if args.stage == "frames" and aspect == "9:16":
        for key in jobs:
            w, h = size_of(proj / "assets/images" / f"{key}.png")
            if h and abs(w / h - 9 / 16) > 0.01:
                print(f"{key}: returned {w}x{h}, not 9:16. Keep the original and ask before cropping.")
    print(tracker.cost_snapshot())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
