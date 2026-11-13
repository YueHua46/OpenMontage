#!/usr/bin/env python3
"""Review loop for generated shots: checklist coverage, first-frame gate, dense contact pages,
zoomed regions, consistent verdicts and bounded retakes. The agent is the reviewer.

Run from the OpenMontage repo with its venv (paid generation still goes through batch_generate.py):
  python -X utf8 <S>/review_shots.py lint   --project <id> --spec shots.json
  python -X utf8 <S>/review_shots.py sheet  --project <id> --spec shots.json --stage frames|videos [--only a1] [--take 2] [--lang es]
  python -X utf8 <S>/review_shots.py mark   --project <id> --spec shots.json a1 --stage frames|videos --verdict pass|fail \
         --observed "what the images show" --ok rest --failed <ids> [--new-check motion:"..."]
  python -X utf8 <S>/review_shots.py retake --project <id> --spec shots.json a1 --reason "..." --prompt-file f.txt [--new-frame --frame-prompt-file g.txt]
  python -X utf8 <S>/review_shots.py status --project <id> --spec shots.json
  python -X utf8 <S>/review_shots.py accept --project <id> --spec shots.json a1 --take 4 --use 0-1.6 --note "user: ..."
         (only on the user's decision: selects a failed take, keeps the fail verdict on record)

Spec fields used:
  "checks"            rules for every shot: {"id", "category", "text"}
  shot "checks"       rules for one shot
  shot "na"           {"category": "why it does not apply to this shot"}
  shot "regions"      {"name": [x, y, w, h]} fractions of the frame to zoom into: the phone screen, the
                      product, parts that must stay still, hands. Every sampled frame is cropped and enlarged.
                      {"name": {"box": [...], "ref": "@m-elder", "ref_box": [...]}} puts the same part of a
                      reference image first in the strip (labelled REF), e.g. the character sheet's face.
  shot "kind": "reference"  a still other shots build on (character sheet, room plate): frames only.
  shot "dialogue", "purpose", spec "max_retakes" (paid retakes per shot after take 1, default 2)

The script only makes the evidence big enough to see (dense pages, enlarged regions, ASR text) and
refuses verdicts that contradict the record. It judges nothing itself and never edits generated pixels.
"""
import argparse
import datetime as dt
import difflib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch_generate import frame_file, is_reference, resolve, take_name  # noqa: E402

# Every shot must answer each category with a check or a reason in "na". The categories are generic,
# so defects nobody listed in advance still get looked at.
CATEGORIES = {
    "count": "how many of each product, part, person and prop; nothing extra, nothing missing",
    "structure": "product shape and how parts connect, matching the reference photos",
    "motion": "which parts may move and how far; everything else stays still relative to the wall or table",
    "orientation": "which way devices, screens and hands face; the side being touched is the right side",
    "viewpoint": "every picture-in-picture (phone, monitor feed) is what that camera can see from where it is",
    "light": "lamps, LEDs, screens, day or night state",
    "text_ui": "on-screen text, app UI and packaging stay as in the reference; no garbled text or watermarks",
    "people": "same identity and clothes; hands, fingers, limbs and faces are natural",
    "action": "the scripted action happens, in order, within the shot",
    "audio": "dialogue, language and sound; the agent cannot hear, so ASR plus the user",
    "continuity": "same person (face, age, hair, clothes), same room and the same furniture and prop positions as the "
                  "reference shots and neighbouring shots; distances the story relies on still hold",
    "realism": "the look matches the chosen style template (references/styles/), e.g. phone footage of a real home for "
               "UGC ads; no unintended AI look: plastic skin, posed bodies, showroom-perfect rooms, render-like products",
}
FPS = 6
TILE_W = 480
# Everything the agent looks at is a JPEG at most this many pixels on the long edge. Agent hosts
# resend every viewed image as base64 on each later request; full-size PNG frames (2-3 MB each)
# pile up until the API gateway rejects the request (HTTP 413). Vision models downscale to about
# this size anyway, so larger files add bytes, not detail. Zoom with `regions` instead.
VIEW_EDGE = 1280
VIEW_QUALITY = 85


def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def dump(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def norm_checks(items):
    out = []
    for n, item in enumerate(items or [], 1):
        out.append(item if isinstance(item, dict) else {"id": f"c{n}", "category": None, "text": str(item)})
    return out


VIDEO_ONLY = {"motion", "action", "audio"}  # a still first frame cannot show these


def checks_for(spec, shot, stage="videos"):
    checks = norm_checks(spec.get("checks")) + norm_checks(shot.get("checks"))
    still = stage != "videos" or is_reference(shot)
    return [c for c in checks if not still or c.get("category") not in VIDEO_ONLY]


def takes_on_disk(proj, sid):
    found = {}
    for path in (proj / "assets/video").glob(f"{sid}*.mp4"):
        if path.stem == sid:
            found[1] = path
        else:
            m = re.fullmatch(re.escape(sid) + r"-take(\d+)", path.stem)
            if m:
                found[int(m[1])] = path
    return dict(sorted(found.items()))


def job_prompt(path):
    job = load(Path(str(path) + ".relay-job.json"), {}) or {}
    return (job.get("request_summary") or {}).get("prompt"), job.get("tool"), job.get("estimated_cost_usd")


class Review:
    def __init__(self, project, spec_path):
        self.proj = Path("projects") / project
        if not self.proj.exists():
            sys.exit(f"Project not found from {Path.cwd()}: {self.proj}")
        self.spec_path = Path(spec_path)
        self.spec = load(self.spec_path)
        self.path = self.proj / "review.json"
        self.data = load(self.path, {"shots": {}})
        self.max_retakes = int(self.spec.get("max_retakes", 2))
        self.sync()

    def shot(self, sid):
        for s in self.spec["shots"]:
            if s["id"] == sid:
                return s
        sys.exit(f"no shot {sid} in {self.spec_path}")

    def sync(self):
        """Record every take and first frame on disk, with the prompt actually sent (from its job file)."""
        for s in self.spec["shots"]:
            rec = self.data["shots"].setdefault(s["id"], {"takes": {}, "selected": None})
            rec.setdefault("frames", {})
            for take, path in takes_on_disk(self.proj, s["id"]).items():
                t = rec["takes"].setdefault(str(take), {"verdict": "pending", "notes": "", "failed_checks": []})
                prompt, tool, cost = job_prompt(path)
                t.update({"clip": path.stem, "file": str(path), "prompt": prompt or t.get("prompt"), "tool": tool or t.get("tool"),
                          "estimated_cost_usd": cost if cost is not None else t.get("estimated_cost_usd")})
            frame = frame_file(self.proj, s)
            if frame.exists():
                f = rec["frames"].setdefault(str(s.get("frame_take", 1)), {"verdict": "pending", "notes": "", "failed_checks": []})
                if f.get("file") and Path(f["file"]).resolve() != frame.resolve():
                    rec.setdefault("frame_source_changes", []).append({"frame_take": s.get("frame_take", 1),
                        "previous": dict(f), "new_file": str(frame), "at": now()})
                    f.clear()
                    f.update({"verdict": "pending", "notes": "Frame source changed; review the new input.", "failed_checks": []})
                f.update({"clip": frame.stem, "file": str(frame)})

    def latest(self, sid, stage="videos"):
        items = self.data["shots"][sid]["takes" if stage == "videos" else "frames"]
        if stage == "frames":
            key = str(self.shot(sid).get("frame_take", 1))
            return (key, items[key]) if key in items else (None, None)
        return (max(items, key=int), items[max(items, key=int)]) if items else (None, None)

    def save(self):
        self.data["updated"] = now()
        dump(self.path, self.data)


# ---------- evidence ----------

def normalize(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"\w+", text)


def transcribe(path, lang):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    model = transcribe.model = getattr(transcribe, "model", None) or WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(path), language=lang, vad_filter=True, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def box(frac, w, h):
    x, y, bw, bh = frac
    return int(x * w), int(y * h), int((x + bw) * w), int((y + bh) * h)


def extract(video, folder, fps):
    """Full-resolution frames at `fps`, named by index; returns [(seconds, path)]."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", f"fps={fps}", "-q:v", "2",
                    str(folder / "f_%04d.jpg")], check=False)
    return [(round(i / fps, 2), p) for i, p in enumerate(sorted(folder.glob("f_*.jpg")))]


def view_copy(src, out, edge=VIEW_EDGE):
    """A JPEG copy of an image for the agent to look at, long edge at most `edge`."""
    from PIL import Image

    im = Image.open(src).convert("RGB")
    im.thumbnail((edge, edge))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=VIEW_QUALITY)
    return str(out)


def pages(frames, out_prefix, cols=3, rows=2, width=TILE_W):
    """Labelled contact pages large enough to read hands, screens and small parts."""
    from PIL import Image, ImageDraw

    made = []
    per = cols * rows
    for start in range(0, len(frames), per):
        chunk = frames[start:start + per]
        tiles = []
        for t, p in chunk:
            im = Image.open(p).convert("RGB")
            im = im.resize((width, int(im.height * width / im.width)))
            ImageDraw.Draw(im).rectangle((0, 0, 92, 26), fill="black")
            ImageDraw.Draw(im).text((6, 6), f"{t:.2f}s", fill="white")
            tiles.append(im)
        h = max(i.height for i in tiles)
        page = Image.new("RGB", (cols * width, rows * h), "#222")
        for n, im in enumerate(tiles):
            page.paste(im, ((n % cols) * width, (n // cols) * h))
        path = Path(f"{out_prefix}_{len(made) + 1:02d}.jpg")
        page.save(path, quality=VIEW_QUALITY)
        made.append(str(path))
    return made


def region_spec(value):
    """A region is [x, y, w, h], or {"box": [...], "ref": "@shot or path", "ref_box": [...]} to put the
    same part of a reference image (e.g. the character sheet's face) first in the strip for comparison."""
    if isinstance(value, dict):
        return value["box"], value.get("ref"), value.get("ref_box") or value["box"]
    return value, None, None


def region_pages(frames, frac, folder, width=360):
    """Paginate a crop instead of shrinking a whole sequence into one unreadable strip."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    cropped = []
    for i, (t, path) in enumerate(frames):
        with Image.open(path) as im:
            crop = im.convert("RGB").crop(box(frac, im.width, im.height))
        crop.thumbnail((width, 560))
        target = folder / f"crop_{i:04d}.jpg"
        crop.save(target, quality=VIEW_QUALITY)
        cropped.append((t, target))
    # Keep actual viewed pages within the documented 1280px limit.
    if not cropped:
        return []
    with Image.open(cropped[0][1]) as im:
        tile_width = min(width, im.width)
        tile_height = im.height
    return pages(cropped, folder / "page", cols=3,
                 rows=max(1, min(2, 1280 // max(1, tile_height))), width=tile_width)


def strip(frames, frac, out, height=360, per_row=6, ref=None):
    """One region cropped from every sampled frame and enlarged, so small details are readable.
    `ref` = (image path, box): that crop comes first, labelled REF, for identity and layout checks."""
    from PIL import Image, ImageDraw

    crops = []
    if ref and Path(ref[0]).exists():
        im = Image.open(ref[0]).convert("RGB")
        c = im.crop(box(ref[1], im.width, im.height))
        c = c.resize((max(1, int(c.width * height / max(1, c.height))), height))
        ImageDraw.Draw(c).rectangle((0, 0, 44, 20), fill="red")
        ImageDraw.Draw(c).text((4, 4), "REF", fill="white")
        crops.append(c)
    for t, p in frames:
        im = Image.open(p).convert("RGB")
        c = im.crop(box(frac, im.width, im.height))
        c = c.resize((max(1, int(c.width * height / max(1, c.height))), height))
        ImageDraw.Draw(c).text((4, 4), f"{t:.2f}s", fill="yellow")
        crops.append(c)
    if not crops:
        return None
    w = max(c.width for c in crops)
    per_row = min(per_row, len(crops))  # a still frame gives one or two crops; no empty columns
    rows = (len(crops) + per_row - 1) // per_row
    sheet = Image.new("RGB", (per_row * w, rows * height), "#222")
    for n, c in enumerate(crops):
        sheet.paste(c, ((n % per_row) * w, (n // per_row) * height))
    sheet.thumbnail((2048, 2048))  # vision models downscale past this; extra pixels only add bytes
    sheet.save(out, quality=VIEW_QUALITY)
    return str(out)


# ---------- commands ----------

def cmd_lint(r, args):
    problems = 0
    globals_ = norm_checks(r.spec.get("checks"))
    ids = [c["id"] for c in globals_]
    for c in globals_:
        if c.get("category") not in CATEGORIES:
            print(f"global check {c['id']}: category {c.get('category')!r} is not one of {list(CATEGORIES)}")
            problems += 1
    for s in r.spec["shots"]:
        own = norm_checks(s.get("checks"))
        ids += [c["id"] for c in own]
        bad = [c["id"] for c in own if c.get("category") not in CATEGORIES]
        covered = {c["category"] for c in globals_ + own} | set(s.get("na", {}))
        needed = [k for k in CATEGORIES if not (is_reference(s) and k in VIDEO_ONLY)]
        missing = [k for k in needed if k not in covered]
        if bad or missing:
            problems += 1
            print(f"{s['id']} ({s.get('purpose', '')}):")
            if bad:
                print("  checks without a valid category:", bad)
            for k in missing:
                print(f"  no check or na reason for {k}: {CATEGORIES[k]}")
        if not is_reference(s) and not any(c["category"] == "motion" for c in own) and "motion" not in s.get("na", {}):
            print(f"{s['id']}: no shot-level motion rule; say which parts move and which must stay still")
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        print("duplicate check ids:", dup)
        problems += 1
    if problems:
        sys.exit(f"{problems} problem(s). Every category needs a check or a reason in the shot's \"na\".")
    print("checklist complete")


def cmd_sheet(r, args):
    stage = args.stage
    if stage == "videos" and args.fps < FPS:
        print(f"WARNING: {args.fps} fps is below the default {FPS}. Gaps this wide hide how an action moves "
              f"(a slow sit-down looks like a fall); do not pass motion or action checks from these pages.")
    for s in r.spec["shots"]:
        sid = s["id"]
        if args.only and sid not in args.only:
            continue
        if stage == "videos" and is_reference(s):
            continue  # reference stills are reviewed with --stage frames
        n, item = r.latest(sid, stage)
        if args.take:  # an earlier take, e.g. to review an existing project or compare versions
            n = str(args.take)
            item = r.data["shots"][sid]["takes" if stage == "videos" else "frames"].get(n)
        if not item:
            print(f"{sid}: no {stage[:-1]} {'take ' + n if args.take else ''} generated yet")
            continue
        if item["verdict"] != "pending" and not args.only:
            continue
        evidence_id = f"{sid}-shared-frame{s.get('frame_take', 1)}" if stage == "frames" and s.get("frame_alias") else item["clip"]
        folder = r.proj / "review" / evidence_id
        evidence = {"pages": [], "regions": {}, "frame": None}
        previous_evidence = item.get("evidence") or {}
        if previous_evidence.get("asr") is not None:
            evidence["asr"] = previous_evidence["asr"]
        if stage == "frames":
            evidence["source"] = item["file"]
            evidence["frame"] = view_copy(item["file"], folder / "frame.jpg")
            frames = [(0.0, Path(item["file"]))]
        else:
            frames = extract(Path(item["file"]), folder / "frames", args.fps)
            evidence["pages"] = pages(frames, folder / "page", cols=args.cols, rows=args.rows, width=args.tile_width)
            evidence["duration"] = frames[-1][0] if frames else 0
            evidence["sampling_fps"] = args.fps
            evidence["source_video"] = item["file"]
        folder.mkdir(parents=True, exist_ok=True)
        for name, value in s.get("regions", {}).items():
            frac, ref, ref_box = region_spec(value)
            ref_img = resolve(r.proj, ref, r.spec) if ref else None
            if ref_img and Path(ref_img).resolve() == Path(item["file"]).resolve():
                ref_img = None  # the reference shot itself
            evidence["regions"][name] = strip(frames, frac, folder / f"region_{name}.jpg",
                                              ref=(ref_img, ref_box) if ref_img else None)
            if stage == "videos" and args.region_pages and name in args.region_pages:
                evidence.setdefault("region_pages", {})[name] = region_pages(
                    frames, frac, folder / f"pages_{name}")
        if stage == "videos" and s.get("dialogue") and args.lang:
            heard = transcribe(item["file"], args.lang)
            evidence["asr"] = None if heard is None else {
                "heard": heard, "match": round(difflib.SequenceMatcher(None, normalize(s["dialogue"]), normalize(heard), autojunk=False).ratio(), 3)}
        item["evidence"] = evidence
        print(f"\n== {item['clip']} ({s.get('purpose', '')})")
        if evidence["frame"]:
            print("frame:", evidence["frame"], f"(review copy; the original {evidence['source']} is not for viewing)")
        for p in evidence["pages"]:
            print("page:", p)
        for name, p in evidence["regions"].items():
            print(f"zoom {name}:", p)
        for name, paths in evidence.get("region_pages", {}).items():
            for p in paths:
                print(f"zoom page {name}:", p)
        if "asr" in evidence:
            print("dialogue:", "faster-whisper not installed, check by ear" if evidence["asr"] is None else evidence["asr"])
        print("checks (read only after writing what you observed):")
        for c in checks_for(r.spec, s, stage):
            print(f"  [{c['id']}] ({c.get('category')}) {c['text']}")
    r.save()
    print("\nReview protocol: look at every page and zoom first and write what you see without re-reading the prompt "
          "(counts, what moved, which way things face, what each screen shows, hands, text). Then compare with the checks "
          "and record it with `mark`. Anything wrong that no check covers is still a failure: add it with --new-check.\n"
          "View only the files printed above (JPEG, long edge <= 1280). Never open the original PNG/MP4 frames with an "
          "image viewer and never use detail \"original\": every viewed image is resent on each later request. "
          "For a closer look add a `regions` box and rerun `sheet --only <id>`.")


def parse_new_check(raw, sid, existing):
    if ":" not in raw:
        sys.exit("--new-check needs category:text")
    cat, text = raw.split(":", 1)
    if cat not in CATEGORIES:
        sys.exit(f"--new-check category must be one of {list(CATEGORIES)}")
    n = 1
    while f"{sid}_{cat}{n}" in existing:
        n += 1
    return {"id": f"{sid}_{cat}{n}", "category": cat, "text": text.strip(), "added": now()}


def cmd_mark(r, args):
    shot = r.shot(args.shot)
    rec = r.data["shots"][args.shot]
    items = rec["takes" if args.stage == "videos" else "frames"]
    n = str(args.take) if args.take else r.latest(args.shot, args.stage)[0]
    if not n or n not in items:
        sys.exit(f"{args.shot}: {args.stage[:-1]} take {n} not found on disk")
    item = items[n]
    if not item.get("evidence"):
        sys.exit(f"{item['clip']}: run `sheet --stage {args.stage}` and look at the evidence before a verdict")
    if not args.observed or len(args.observed.strip()) < 20:
        sys.exit("--observed is required: describe what the images show before judging (counts, motion, facing, screens, hands)")

    added = []
    existing = {c["id"] for c in checks_for(r.spec, shot)}
    for raw in args.new_check:
        c = parse_new_check(raw, args.shot, existing | {a["id"] for a in added})
        added.append(c)
    if added:
        target = r.spec if args.new_check_global else shot
        target.setdefault("checks", []).extend(added)
        dump(r.spec_path, r.spec)
    checks = {c["id"] for c in checks_for(r.spec, shot, args.stage)}

    failed = set(args.failed) | {c["id"] for c in added}
    ok = checks - failed if args.ok == ["rest"] else set(args.ok)
    unknown = (failed | ok) - checks
    if unknown:
        sys.exit(f"unknown check ids {sorted(unknown)}; known: {sorted(checks)}")
    if ok & failed:
        sys.exit(f"checks both ok and failed: {sorted(ok & failed)}")
    unanswered = checks - ok - failed
    if unanswered:
        sys.exit(f"answer every check (--ok ids or --ok rest, --failed ids); unanswered: {sorted(unanswered)}")

    # Consistency rules: the verdict must agree with the recorded evidence.
    if args.verdict == "pass" and failed:
        sys.exit(f"a passing take cannot have failed checks: {sorted(failed)}")
    if args.verdict == "fail" and not failed:
        sys.exit("a failing take needs the failed check ids, or --new-check category:text for a defect no check covers")
    asr = (item.get("evidence") or {}).get("asr")
    if args.verdict == "pass" and asr and asr.get("match", 1) < 0.9 and not args.flag_note:
        sys.exit(f"dialogue match {asr['match']} < 0.9: explain with --flag-note or fail it")

    item.update({"verdict": args.verdict, "observed": args.observed, "notes": args.note or "", "failed_checks": sorted(failed),
                 "ok_checks": sorted(ok), "flag_note": args.flag_note, "reviewed": now()})
    if args.stage == "videos":
        if args.verdict == "pass":
            rec["selected"] = item["clip"]
        elif rec.get("selected") == item["clip"]:
            rec["selected"] = None
    r.save()
    print(f"{item['clip']} ({args.stage[:-1]}): {args.verdict}" + (f"; new checks {[c['id'] for c in added]} saved to the spec" if added else ""))


def cmd_retake(r, args):
    shot = r.shot(args.shot)
    rec = r.data["shots"][args.shot]
    n, take = r.latest(args.shot)
    frame_key, frame = r.latest(args.shot, "frames")
    if args.new_frame and frame and frame["verdict"] == "fail" and not take:
        take = None  # a failed first frame can be redone before any video exists
    elif not take:
        sys.exit(f"{args.shot}: no video yet; for a failed first frame use --new-frame")
    elif take["verdict"] != "fail":
        sys.exit(f"{take['clip']} is {take['verdict']}; mark it fail with the defect before a retake")
    used = len(rec.get("retakes", []))  # one retake = one decision to regenerate (frame, video or both)
    limit = int(shot.get("max_retakes", r.max_retakes))
    if used >= limit:
        rec["status"] = "awaiting_human_review"
        r.save()
        sys.exit(f"{args.shot}: {used} retakes used (max {limit}). Report it as failed / awaiting human review "
                 f"with the defects and tried prompts; more retakes need the user's approval (raise max_retakes in the spec).")
    if not args.reason:
        sys.exit("--reason is required: name the defect and what the new input changes")
    if args.prompt_file:
        shot["video_prompt"] = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if args.new_frame:
        if not args.frame_prompt_file:
            sys.exit("--new-frame needs --frame-prompt-file")
        shot["frame_prompt"] = Path(args.frame_prompt_file).read_text(encoding="utf-8").strip()
        shot["frame_take"] = int(shot.get("frame_take", 1)) + 1
    if take:
        shot["take"] = int(n) + 1
    rec.setdefault("retakes", []).append({"from": (take or frame)["clip"], "to_take": shot.get("take", 1), "to_frame_take": shot.get("frame_take", 1),
                                          "reason": args.reason, "prompt_changed": bool(args.prompt_file), "new_frame": bool(args.new_frame), "at": now()})
    dump(r.spec_path, r.spec)
    r.save()
    if args.new_frame:
        nxt = "--stage frames, then review the new frame" + ("" if is_reference(shot) else " before --stage videos")
        name = take_name(args.shot, shot.get("frame_take", 1))
    else:
        nxt, name = "--stage videos", take_name(args.shot, shot.get("take", 1))
    print(f"{args.shot}: next is {name} ({used + 1}/{limit} retakes). "
          f"Run batch_generate.py {nxt} --only {args.shot}; earlier takes stay on disk. Approved limit: {limit}.")


def cmd_accept(r, args):
    """The user decides to use a take the review failed (e.g. only part of it). The fail verdict and its failed
    checks stay on record; only the selection and the user's decision are added. The agent never calls this
    on its own judgement."""
    rec = r.data["shots"][args.shot]
    n = str(args.take) if args.take else r.latest(args.shot)[0]
    if not n or n not in rec["takes"]:
        sys.exit(f"{args.shot}: video take {n} not found")
    take = rec["takes"][n]
    if not args.note or len(args.note.strip()) < 10:
        sys.exit("--note is required: quote the user's decision and how the take is used (e.g. trimmed range)")
    use = None
    if args.use:
        a, b = (float(x) for x in args.use.split("-"))
        use = [a, b]
    take["user_accepted"] = {"note": args.note, "use_seconds": use, "at": now(), "verdict_kept": take.get("verdict"),
                             "failed_checks": take.get("failed_checks", [])}
    rec["selected"] = take["clip"]
    r.save()
    print(f"{take['clip']}: selected as accepted by the user (review verdict stays {take.get('verdict')})"
          + (f", use {use[0]}-{use[1]} s" if use else ""))


def cmd_status(r, args):
    rows, unresolved, clips = [], [], []
    for s in r.spec["shots"]:
        sid = s["id"]
        rec = r.data["shots"][sid]
        n, latest = r.latest(sid)
        _, frame = r.latest(sid, "frames")
        if is_reference(s):
            rows.append({"shot": sid, "purpose": s.get("purpose", ""), "kind": "reference",
                         "frame": frame["verdict"] if frame else "none", "frame_take": s.get("frame_take", 1)})
            continue
        used = len(rec.get("retakes", []))  # one retake = one decision to regenerate (frame, video or both)
        rows.append({"shot": sid, "purpose": s.get("purpose", ""), "frame": frame["verdict"] if frame else "none",
                     "takes": len(rec["takes"]), "latest": n, "verdict": latest["verdict"] if latest else "no take",
                     "retakes_left": max(0, int(s.get("max_retakes", r.max_retakes)) - used), "selected": rec.get("selected"), "status": rec.get("status"),
                     "user_accepted": next((t["user_accepted"] for t in rec["takes"].values()
                                            if t.get("clip") == rec.get("selected") and t.get("user_accepted")), None)})
        (clips if rec.get("selected") else unresolved).append(rec.get("selected") or sid)
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    print("Accepted clips for edit.json:", clips)
    if unresolved:
        print("Not accepted yet:", unresolved)
    r.save()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lint", "status", "sheet", "mark", "retake", "accept"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--spec", required=True)
        if name in ("sheet", "mark"):
            p.add_argument("--stage", choices=("frames", "videos"), default="videos")
        if name == "sheet":
            p.add_argument("--only", nargs="*")
            p.add_argument("--lang", help="dialogue language for the ASR check, e.g. es, en, zh")
            p.add_argument("--fps", type=float, default=FPS, help=f"sampled frames per second (default {FPS}); inspect rapid transitions more densely")
            p.add_argument("--cols", type=int, default=3, help="contact-sheet columns; use small tiles only with region zooms")
            p.add_argument("--rows", type=int, default=2, help="contact-sheet rows")
            p.add_argument("--tile-width", type=int, default=TILE_W, help="contact-sheet tile width in pixels")
            p.add_argument("--region-pages", nargs="*", help="region names to paginate at readable size, instead of relying on a shrunken strip")
        if name in ("sheet", "mark", "accept"):
            p.add_argument("--take", type=int, help="a specific take instead of the latest")
        if name in ("mark", "retake", "accept"):
            p.add_argument("shot")
        if name == "accept":
            p.add_argument("--note", help="the user's decision, quoted, and how the take is used")
            p.add_argument("--use", help="seconds of the take to use, e.g. 0-1.6")
        if name == "mark":
            p.add_argument("--verdict", choices=("pass", "fail"), required=True)
            p.add_argument("--observed", help="what the images show, written before reading the checks")
            p.add_argument("--ok", nargs="*", default=[], help="ids of checks that hold, or `rest`")
            p.add_argument("--failed", nargs="*", default=[], help="ids of failed checks")
            p.add_argument("--new-check", nargs="*", default=[], help="category:text for a defect no check covers")
            p.add_argument("--new-check-global", action="store_true", help="add the new checks to every shot")
            p.add_argument("--flag-note", help="why a low dialogue match does not fail the take")
            p.add_argument("--note")
        if name == "retake":
            p.add_argument("--reason")
            p.add_argument("--prompt-file")
            p.add_argument("--new-frame", action="store_true")
            p.add_argument("--frame-prompt-file")
    args = ap.parse_args()
    r = Review(args.project, args.spec)
    {"lint": cmd_lint, "status": cmd_status, "sheet": cmd_sheet, "mark": cmd_mark, "retake": cmd_retake,
     "accept": cmd_accept}[args.cmd](r, args)


if __name__ == "__main__":
    main()
