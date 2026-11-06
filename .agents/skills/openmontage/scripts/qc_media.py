#!/usr/bin/env python3
"""Measure clips or a final render: size, duration, decode errors, cuts, sound windows, loudness, contact sheet.

Usage: python qc_media.py <file-or-folder> [...] [--sheets DIR]
It measures only. It cannot judge pronunciation, lip sync or how the audio sounds.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path


def text(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8").stdout
    return json.loads(out)


def check(path, sheets):
    info = probe(path)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(info.get("format", {}).get("duration", 0))
    row = {"file": Path(path).name, "size": f"{video.get('width')}x{video.get('height')}", "fps": video.get("r_frame_rate"), "duration": round(duration, 3), "audio": has_audio}
    row["decode_errors"] = len(text(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"]).strip().splitlines())
    log = text(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "select='gt(scene,0.25)',showinfo", "-an", "-f", "null", "-"])
    row["cuts"] = [round(float(x), 2) for x in re.findall(r"pts_time:([\d.]+)", log)]
    if has_audio:
        log = text(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "silencedetect=n=-35dB:d=0.25,ebur128=peak=true", "-f", "null", "-"])
        marks = [(k, float(v)) for k, v in re.findall(r"silence_(start|end): ([\d.]+)", log)]
        windows, pos = [], 0.0
        for kind, at in marks:
            if kind == "start":
                if at > pos + 0.05:
                    windows.append([round(pos, 2), round(at, 2)])
            else:
                pos = at
        if pos < duration - 0.05 and (not marks or marks[-1][0] == "end"):
            windows.append([round(pos, 2), round(duration, 2)])
        row["sound_windows"] = windows
        i = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
        p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", log)
        row["lufs"] = float(i[-1]) if i else None
        row["peak_db"] = float(p[-1]) if p else None
    if sheets and duration:
        Path(sheets).mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", f"fps=6/{duration},scale=200:-1,tile=6x1", "-frames:v", "1",
                        str(Path(sheets) / (Path(path).stem + ".jpg"))])
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--sheets")
    args = ap.parse_args()
    files = []
    for raw in args.paths:
        p = Path(raw)
        files += sorted(p.glob("*.mp4")) if p.is_dir() else [p]
    for f in files:
        print(json.dumps(check(f, args.sheets), ensure_ascii=False))


if __name__ == "__main__":
    main()
