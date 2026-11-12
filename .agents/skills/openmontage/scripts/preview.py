#!/usr/bin/env python3
"""Small JPEG copies of images or video frames for the agent to look at.

Agent hosts (Codex, Claude Code) resend every image the agent has viewed as base64 on each later
request. Full-size PNG frames are 2-3 MB each; a few dozen of them push a request past the API
gateway's body limit (HTTP 413 Payload Too Large) long before the context window is full. Vision
models downscale to roughly 1280-2048 px anyway, so a bigger file adds bytes, not detail.

Use this for anything not covered by review_shots.py: user source photos, product references,
a quick look at a clip.

  python -X utf8 <S>/preview.py <image|video|folder> ... [--out DIR] [--edge 1280]
  python -X utf8 <S>/preview.py clip.mp4 --times 0.5 2 3.5

Prints each preview path with its size. Never view the originals themselves.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_shots import VIEW_EDGE, view_copy  # noqa: E402

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def video_frames(video, times, out, edge):
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in times:
            raw = Path(tmp) / f"{t:.2f}.png"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video),
                            "-frames:v", "1", str(raw)], check=False)
            if raw.exists():
                made.append(view_copy(raw, out / f"{video.stem}_{t:05.2f}s.jpg", edge))
            else:
                print(f"{video}: no frame at {t}s", file=sys.stderr)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", help="output folder (default: a _preview folder next to each input)")
    ap.add_argument("--edge", type=int, default=VIEW_EDGE, help=f"long edge in pixels (default {VIEW_EDGE})")
    ap.add_argument("--times", type=float, nargs="*", default=[0.5], help="seconds to sample from videos")
    args = ap.parse_args()

    files = []
    for raw in args.inputs:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT | VIDEO_EXT)
        elif p.exists():
            files.append(p)
        else:
            print(f"not found: {p}", file=sys.stderr)
    for f in files:
        out = Path(args.out) if args.out else f.parent / "_preview"
        if f.suffix.lower() in VIDEO_EXT:
            made = video_frames(f, args.times, out, args.edge)
        elif f.suffix.lower() in IMAGE_EXT:
            made = [view_copy(f, out / f"{f.stem}.jpg", args.edge)]
        else:
            print(f"skipped (not an image or video): {f}", file=sys.stderr)
            continue
        for m in made:
            print(f"{m}  ({Path(m).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
