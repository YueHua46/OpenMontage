#!/usr/bin/env python3
"""Generate first frames or clips from shot specs, with cost tracking; several projects run concurrently.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/batch_generate.py --project <id> --spec shots.json --stage frames --dry-run
  python -X utf8 <skill>/scripts/batch_generate.py --project <id> --spec shots.json --stage videos --only a1 a2
  python -X utf8 <skill>/scripts/batch_generate.py --project v1 v2 v3 --stage videos     # specs: projects/<id>/shots.json

Batch: every shot of every listed project is submitted at once in one pool. A model can set
limits.max_concurrent in the relay config when its relay rejects parallel tasks; --workers overrides it.
Each project keeps its own cost log, budget, first-frame gate and skip-existing behaviour; all budgets are
reserved before anything is submitted.

Spec (paths relative to the project folder unless absolute):
{"budget_usd": 4.0, "resolution": "720p", "aspect_ratio": "9:16", "video_tool": "...", "image_tool": "...",
 "video_inputs": {"extra_params": {...}},
 "shots": [{"id": "a1", "refs": ["assets/images/source/x.png"], "frame_prompt": "...", "duration": 6,
            "video_prompt": "...", "take": 1, "frame_take": 1}]}

- A shot with no refs uses text-to-image.
- A shot can set "aspect_ratio" (e.g. a 16:9 surveillance feed shown inside a 9:16 app screen) for its
  frame and video.
- A shot can set "video_tool" and "resolution" to move just that shot to another approved provider
  (its limits differ, e.g. MiniMax H3 is 768P and 4-15 s). Generate it in its own --only run.
- "kind": "reference" marks a still other shots build on (character sheet, room plate); it needs no
  video_prompt and is skipped at --stage videos. Other shots name it in refs as "@<id>", which always
  means its current frame take.
- take N > 1 writes assets/video/<id>-takeN.mp4 (frame_take likewise for frames), so earlier takes are kept.
  review_shots.py retake bumps these numbers; do not overwrite an earlier take.
- frame_alias is an explicit local shared first-frame path for scripts requiring identical stills;
  frames generation skips the alias, but each shot still needs its own frame review before video.
- video_inputs (spec level, then shot level) is merged into the video tool inputs, e.g. extra_params,
  last_frame_path ("same" = the shot's first frame, "@<id>" = a reference shot's current frame, e.g. an
  end pose), reference_image_paths, operation.
- Resolution, aspect ratio and price come from the tool's relay config, so any configured model works.
Existing outputs are skipped. --resubmit is a new paid attempt and needs user approval.
"""
import argparse
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from lib.config_model import BudgetMode  # noqa: E402
from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError, CostTracker  # noqa: E402
from tools.tool_registry import registry  # noqa: E402

# Fallbacks only; the live defaults come from routing.default_tools in the relay config,
# so switching models (python -m tools.relay route ...) needs no script change.
FALLBACK_IMAGE_TOOL = "relay_tststs_gpt_image_2_6"
FALLBACK_VIDEO_TOOL = "relay_tststs_grok_video_1_5"


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
    if shot.get("frame_alias"):
        value = Path(shot["frame_alias"])
        return value if value.is_absolute() else proj / value
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


def resolve(proj, value, spec=None):
    """A path relative to the project, or "@<shot id>" for that shot's current first frame
    (its latest frame_take), so shots that use a reference shot follow its retakes."""
    if spec is not None and isinstance(value, str) and value.startswith("@"):
        sid = value[1:]
        for s in spec["shots"]:
            if s["id"] == sid:
                return str(frame_file(proj, s))
        sys.exit(f"ref {value}: no shot {sid} in the spec")
    return value if Path(value).is_absolute() else str(proj / value)


def is_reference(shot):
    """Reference shots (character sheet, room plate) are stills other shots build on; they get no video."""
    return shot.get("kind") == "reference"


def video_extra(proj, spec, shot, frame):
    merged = {**spec.get("video_inputs", {}), **shot.get("video_inputs", {})}
    for key, value in list(merged.items()):
        if key.endswith("_path") and isinstance(value, str):
            merged[key] = str(frame) if value == "same" else resolve(proj, value, spec)
        elif key.endswith("_paths") and isinstance(value, list):
            merged[key] = [str(frame) if v == "same" else resolve(proj, v, spec) for v in value]
    tail = merged.get("last_frame_path")
    if tail and Path(tail).resolve() == Path(frame).resolve() and not shot.get("same_end_frame_reason", "").strip():
        sys.exit(f"{shot['id']}: identical first/last frame can cause return motion. "
                 "Remove last_frame_path unless the intended action really returns to this composition; "
                 "record that source-based reason in same_end_frame_reason. It is not an appearance lock.")
    return merged


def plan_project(args, project, spec_path):
    """Build one project's jobs: a dict with proj, spec, tool_name, tool, aspect and jobs {key: (inputs, cost)}."""
    proj = Path("projects") / project
    if not proj.exists():
        sys.exit(f"Project not found from {Path.cwd()}: {proj}")
    spec_path = Path(spec_path) if spec_path else proj / "shots.json"
    if not spec_path.exists():
        sys.exit(f"{project}: spec not found: {spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    hold = spec.get("production_hold") or {}
    if hold.get("active") and args.stage not in hold.get("allowed_stages", []) and not args.dry_run:
        sys.exit(f"{project}: generation held for quality review: {hold.get('reason', 'unresolved review failure')}. "
                 "Resolve the recorded cause before clearing production_hold; dry-run remains available.")
    image_tool_name = args.image_tool or spec.get("image_tool") or default_tool("image", FALLBACK_IMAGE_TOOL)
    aspect = spec.get("aspect_ratio", "9:16")
    resolution = spec.get("resolution")
    shots = [s for s in spec["shots"] if not args.only or s["id"] in args.only]
    if args.stage == "videos":
        shots = [s for s in shots if not is_reference(s)]
    # A shot may name its own video_tool and resolution (one shot moved to another approved provider).
    # One run uses one tool, so shots on different tools are generated in separate --only runs.
    own_tools = {s["video_tool"] for s in shots if s.get("video_tool")} if args.stage == "videos" else set()
    if len(own_tools) > 1 or (own_tools and any(not s.get("video_tool") for s in shots)):
        sys.exit(f"{project}: the selected shots use different video tools {sorted(own_tools)}; run each group with --only.")
    video_tool_name = (args.video_tool or next(iter(own_tools), None) or spec.get("video_tool")
                       or default_tool("video", FALLBACK_VIDEO_TOOL))
    (proj / "assets/images").mkdir(parents=True, exist_ok=True)
    (proj / "assets/video").mkdir(parents=True, exist_ok=True)

    tool_name = image_tool_name if args.stage == "frames" else video_tool_name
    tool = get_tool(tool_name)
    if args.stage == "videos":
        for s in shots:
            check_limits(tool, s.get("resolution", resolution),
                         s.get("video_inputs", {}).get("aspect_ratio", s.get("aspect_ratio", aspect)))

    # First-frame gate: when the spec has acceptance checks, a video is only made from a frame that
    # review_shots.py marked pass. Frames are cheap; most defects (phone reversed, extra device) start there.
    review = {}
    if args.stage == "videos" and spec.get("checks") and not args.skip_frame_review:
        review = (json.loads((proj / "review.json").read_text(encoding="utf-8")) if (proj / "review.json").exists() else {}).get("shots", {})

    jobs = {}
    for s in shots:
        sid = s["id"]
        if args.stage == "frames" and s.get("frame_alias"):
            print(f"{project}/{sid}: shared first frame {frame_file(proj, s)}; generate and review its owner first.")
            continue
        need = "frame_prompt" if args.stage == "frames" else "video_prompt"
        if not s.get(need):
            sys.exit(f"{project}/{sid}: no {need} in the spec")
        frame = frame_file(proj, s)
        if args.stage == "frames":
            out = frame
            refs = [resolve(proj, r, spec) for r in s.get("refs", [])]
            missing = [r for r in refs if not Path(r).exists()]
            if missing:
                sys.exit(f"{project}/{sid}: missing refs {missing}. Generate and review reference shots first "
                         f"(--only <their ids>), then the shots that use them.")
            inputs = {"preferred_tool": tool_name, "generation_mode": "edit" if refs else "generate",
                      "aspect_ratio": s.get("aspect_ratio", aspect),
                      "output_path": str(out), "scene_id": sid, "prompt": s["frame_prompt"]}
            if refs:
                inputs["image_paths"] = refs
        else:
            out = video_file(proj, s)
            if not frame.exists() and not args.dry_run:
                sys.exit(f"{project}/{sid}: first frame missing: {frame}")
            if spec.get("checks") and not args.skip_frame_review and not args.dry_run and not out.exists():
                verdict = review.get(sid, {}).get("frames", {}).get(str(s.get("frame_take", 1)), {}).get("verdict")
                if verdict != "pass":
                    print(f"{project}/{sid}: first frame {frame.name} is {verdict or 'not reviewed'}; review it (review_shots.py sheet/mark --stage frames) first. Skipped.")
                    continue
                extra = {**spec.get("video_inputs", {}), **s.get("video_inputs", {})}
                referenced = [v[1:] for v in extra.values() if isinstance(v, str) and v.startswith("@")]
                unreviewed = []
                for ref_id in referenced:
                    ref_shot = next((x for x in spec["shots"] if x["id"] == ref_id), None)
                    if not ref_shot or review.get(ref_id, {}).get("frames", {}).get(str(ref_shot.get("frame_take", 1)), {}).get("verdict") != "pass":
                        unreviewed.append(ref_id)
                if unreviewed:
                    print(f"{project}/{sid}: video input references {unreviewed} have not passed frame review. Skipped.")
                    continue
            inputs = {"preferred_tool": tool_name, "operation": "image_to_video", "reference_image_path": str(frame), "duration": s["duration"],
                      "aspect_ratio": s.get("aspect_ratio", aspect), "output_path": str(out), "scene_id": sid,
                      "prompt": s["video_prompt"]}
            if s.get("resolution", resolution):
                inputs["resolution"] = s.get("resolution", resolution)
            inputs.update(video_extra(proj, spec, s, frame))
            lost = [v for k, v in inputs.items() if k.endswith("_path") and k != "output_path" and isinstance(v, str)
                    and not Path(v).exists()]
            if lost and not args.dry_run:
                sys.exit(f"{project}/{sid}: missing video input files {lost}; generate and review them first.")
        if out.exists() and not args.resubmit and not args.dry_run:
            print(f"{project}/{out.stem}: already exists, skipped")
            continue
        if args.resubmit:
            inputs["confirm_resubmit"] = True
        try:
            cost = round(tool.estimate_cost(inputs), 4)  # price from the relay config
        except Exception as exc:
            sys.exit(f"{project}/{sid}: no price for {tool_name}: {exc}")
        jobs[out.stem] = (inputs, cost)
    return {"project": project, "proj": proj, "spec": spec, "tool_name": tool_name, "tool": tool, "jobs": jobs, "aspect": aspect}


def concurrency(args, tool):
    """--workers, else the model's limits.max_concurrent in the relay config, else no cap (all jobs at once)."""
    limit = args.workers or (getattr(getattr(tool, "relay_model", None), "limits", {}) or {}).get("max_concurrent")
    return int(limit) if limit else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", nargs="+", required=True, help="one or more project ids; all run concurrently")
    ap.add_argument("--spec", nargs="*", default=[], help="one spec per project, same order; default projects/<id>/shots.json")
    ap.add_argument("--stage", choices=("frames", "videos"), required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resubmit", action="store_true", help="new paid attempt; only after user approval")
    ap.add_argument("--image-tool")
    ap.add_argument("--video-tool")
    ap.add_argument("--workers", type=int, help="cap concurrent submissions per tool (default: limits.max_concurrent, else no cap)")
    ap.add_argument("--skip-frame-review", action="store_true", help="generate videos from unreviewed frames; only with user approval")
    args = ap.parse_args()
    if args.spec and len(args.spec) != len(args.project):
        sys.exit(f"--spec needs one file per project ({len(args.project)}), or none to use projects/<id>/shots.json")
    if len(set(args.project)) != len(args.project):
        sys.exit("duplicate project ids")

    plans = [plan_project(args, p, args.spec[i] if args.spec else None) for i, p in enumerate(args.project)]
    total = 0.0
    for plan in plans:
        sub = round(sum(c for _, c in plan["jobs"].values()), 4)
        total += sub
        print(f"{plan['project']}: {args.stage} with {plan['tool_name']}: {len(plan['jobs'])} job(s), estimated ${sub}")
    if len(plans) > 1:
        print(f"all {len(plans)} projects: {sum(len(p['jobs']) for p in plans)} job(s), estimated ${round(total, 4)}")
    if not any(p["jobs"] for p in plans):
        return
    selector = registry.get("image_selector" if args.stage == "frames" else "video_selector")

    if args.dry_run:
        for plan in plans:
            for key, (inputs, cost) in plan["jobs"].items():
                label = f"{plan['project']}/{key}"
                if args.stage == "videos" and not Path(inputs["reference_image_path"]).exists():
                    print(label, "dry_run skipped: first frame not generated yet", f"${cost}")
                    continue
                r = selector.execute(dict(inputs, dry_run=True))
                print(label, "dry_run", r.success, f"${cost}", r.error or "")
        return

    # Reserve every project's budget before submitting anything. budget_usd is the budget the user
    # approved, so it is a hard cap: if any project would exceed it, nothing is submitted.
    # The per-shot price was approved with the batch quote, so only the project budget caps it here.
    entries, current, new_reservations = {}, None, set()
    try:
        for plan in plans:
            current = plan["project"]
            tracker = CostTracker(budget_total_usd=plan["spec"].get("budget_usd", 4.0), reserve_pct=0.0,
                                  single_action_approval_usd=float("inf"), mode=BudgetMode.CAP,
                                  cost_log_path=plan["proj"] / "cost_log.json")
            tracker.budget_total_usd = float(plan["spec"].get("budget_usd", 4.0))  # the spec is the approved budget, not the old log
            tracker.approve_tool(plan["tool_name"])
            plan["tracker"] = tracker
            for key, (_, cost) in plan["jobs"].items():
                existing = next((e for e in reversed(tracker.entries)
                                 if e["tool"] == plan["tool_name"] and e["operation"] == key
                                 and e["status"] == "reserved"), None) if not args.resubmit else None
                if existing:
                    entries[(plan["project"], key)] = existing["id"]
                    continue  # resume the same commitment; do not reserve it twice
                entry = tracker.estimate(plan["tool_name"], key, cost)
                entries[(plan["project"], key)] = entry
                tracker.reserve(entry)
                new_reservations.add((plan["project"], key))
    except (BudgetExceededError, ApprovalRequiredError) as exc:
        for plan in plans:
            for key in plan["jobs"]:
                entry = entries.get((plan["project"], key))
                if entry and "tracker" in plan and (plan["project"], key) in new_reservations:
                    try:
                        plan["tracker"].refund(entry)
                    except Exception:
                        pass  # estimated but never reserved
        spent = {p["project"]: p["tracker"].cost_snapshot() for p in plans if "tracker" in p}
        sys.exit(f"Nothing submitted. {current}: {exc}. Spent so far: {spent}. "
                 f"Ask the user before raising budget_usd in that project's spec.")

    # One pool for all projects: every shot of every project is submitted at once,
    # capped per tool only when the relay config (or --workers) sets a limit.
    caps = {plan["tool_name"]: concurrency(args, plan["tool"]) for plan in plans}
    gates = {name: threading.BoundedSemaphore(limit) if limit else None for name, limit in caps.items()}
    work = [(plan, key, inputs) for plan in plans for key, (inputs, _) in plan["jobs"].items()]
    print(f"submitting {len(work)} job(s) concurrently; per-tool cap: {({n: c or 'none' for n, c in caps.items()})}")

    def run(item):
        plan, key, inputs = item
        gate = gates[plan["tool_name"]]
        try:
            if gate:
                gate.acquire()
            r = selector.execute(inputs)
            return plan, key, r.success, r.error, r.cost_usd, (r.data or {})
        except Exception as exc:
            return plan, key, False, repr(exc)[:300], 0, {}
        finally:
            if gate:
                gate.release()

    with ThreadPoolExecutor(max_workers=max(1, len(work))) as pool:
        rows = list(pool.map(run, work))

    failed = 0
    for plan, key, ok, err, cost, data in rows:
        entry = entries[(plan["project"], key)]
        status = data.get("status")
        output = Path(plan["jobs"][key][0]["output_path"])
        job_path = Path(str(output) + ".relay-job.json")
        job = json.loads(job_path.read_text(encoding="utf-8")) if job_path.exists() else {}
        outstanding = status == "pending" or job.get("state") in {"submitting", "submitted", "submit_unknown", "succeeded"}
        outstanding = outstanding or (job.get("state") == "failed" and job.get("billed") == "unknown")
        if ok:
            plan["tracker"].reconcile(entry, cost or plan["jobs"][key][1], True)
        elif outstanding:
            failed += 1  # keep the reservation until the task's billing is known
            status = "pending / budget reserved"
            err = job.get("last_poll_error") or err
        else:
            plan["tracker"].refund(entry)
            failed += 1
        print(f"{plan['project']}/{key}", "ok" if ok else ("PENDING" if outstanding else "FAILED"), status or "", str(err or "")[:200])
    for plan in plans:
        if args.stage == "frames":
            for key, (inputs, _cost) in plan["jobs"].items():
                want = str(inputs.get("aspect_ratio") or plan["aspect"])
                if ":" not in want:
                    continue
                aw, ah = (float(x) for x in want.split(":"))
                w, h = size_of(plan["proj"] / "assets/images" / f"{key}.png")
                if h and abs(w / h - aw / ah) > 0.01:
                    print(f"{plan['project']}/{key}: returned {w}x{h}, not {want}. Keep the original and ask before cropping.")
        print(plan["project"], plan["tracker"].cost_snapshot())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
