#!/usr/bin/env python3
"""Build a HyperFrames workspace from edit.json: trimmed clips, audio mix and index.html.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/assemble.py --project <id> --edit edit.json [--workspace hyperframes]
Then check and render with hyperframes_compose (see references/compose.md).

Edit file, with times in seconds of the source clip:
{
  "segments": [
    {"clip": "a1", "in": 1.0, "out": 6.33},
    {"clip": "a4", "in": 0.5, "out": 3.1, "audio_out": 4.8, "insert": {"id": "ui_events", "duration": 2.05}},
    {"insert": {"id": "ui_release", "duration": 0.8}, "bed": "c5"},
    {"clip": "c5", "in": 0, "out": 3.8, "role": "bed", "label": "scare"}
  ],
  "extra_audio": [{"clip": "c3", "in": 0.33, "out": 0.78, "at": "scare", "offset": 0.2, "effect": "speaker"}],
  "overlay_html": "overlay.html", "overlay_js": "overlay.js"
}
An "insert" is a screen drawn by the overlay, with no clip behind it; the clip's audio keeps playing under it.
A segment with "fit": "contain" is scaled whole into the frame on black (e.g. a square CCTV shot in 9:16)
instead of the default cover-and-crop. "mute": true drops a segment's own sound; "clip_audio": false mutes all.
Narration files: "voiceover": [{"file": "assets/audio/vo1.mp3", "at": "<label>", "offset": 0.05, "tempo": 1.0}]
(paths relative to the project; tempo changes speech speed only, without pitch change).
Overlay files may use {{total}}, {{<id>.start}}, {{<id>.duration}}, {{<id>.end}} and {{<id>.start+0.5}}.
This never alters generated pixels beyond trimming and scaling to the output size.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from tools.tool_registry import registry  # noqa: E402

W, H, FPS = 1080, 1920, 30
SPEAKER = "highpass=f=320,lowpass=f=3400,acompressor=threshold=-20dB:ratio=4,aecho=0.8:0.55:110:0.22"


def q(x):
    return round(round(x * FPS) / FPS, 4)


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def lufs(path, start, end):
    log = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(end - start), "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    return float(found[-1]) if found else -20.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--edit", required=True)
    ap.add_argument("--workspace", default="hyperframes")
    ap.add_argument("--speech-lufs", type=float, default=-16)
    ap.add_argument("--target-lufs", type=float, default=-15.5)
    args = ap.parse_args()

    proj = Path("projects") / args.project
    source = proj / "assets/video"
    ws = proj / args.workspace
    assets = ws / "assets"
    temp = assets / "_audio"
    temp.mkdir(parents=True, exist_ok=True)
    edit_path = Path(args.edit)
    edit = json.loads(edit_path.read_text(encoding="utf-8"))

    t, pictures, sounds, marks = 0.0, [], [], {}
    for n, seg in enumerate(edit["segments"], 1):
        start = t
        if "clip" in seg:
            d = q(seg["out"] - seg["in"])
            name = f"p{n:02d}_{seg['clip']}.mp4"
            pictures.append({"name": name, "clip": seg["clip"], "in": seg["in"], "duration": d, "start": t,
                             "fit": seg.get("fit", "cover")})
            if edit.get("clip_audio", True) and not seg.get("mute"):
                sounds.append({"clip": seg["clip"], "in": seg.get("audio_in", seg["in"]), "out": seg.get("audio_out", seg["out"]), "at": t, "role": seg.get("role", "voice")})
            marks.setdefault(seg.get("label", seg["clip"]), {"start": t, "duration": d})
            t = q(t + d)
        if "insert" in seg:
            ins = seg["insert"]
            d = q(ins["duration"])
            marks[ins["id"]] = {"start": t, "duration": d}
            if seg.get("bed"):
                sounds.append({"clip": seg["bed"], "in": 0, "out": d, "at": t, "role": "bed"})
            t = q(t + d)
    total = t
    marks["total"] = {"start": 0, "duration": total}

    for p in pictures:
        fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
               if p["fit"] == "contain" else f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}")
        ff("-ss", p["in"], "-i", source / f"{p['clip']}.mp4", "-t", p["duration"], "-an", "-vf",
           f"{fit},fps={FPS},format=yuv420p", "-c:v", "libx264", "-crf", 15, "-preset", "fast", assets / p["name"])

    tracks = []
    for k, s in enumerate(sounds):
        path = source / f"{s['clip']}.mp4"
        gain = -14 if s["role"] == "bed" else max(-14, min(10, args.speech_lufs - lufs(path, s["in"], s["out"])))
        out = temp / f"s{k:02d}.wav"
        ff("-ss", s["in"], "-t", round(s["out"] - s["in"], 3), "-i", path, "-vn", "-af", f"volume={gain:.1f}dB", "-ar", 48000, "-ac", 2, out)
        tracks.append({"path": str(out), "role": "primary", "volume": 1.0, "start_seconds": s["at"], "fade_in_seconds": 0.02, "fade_out_seconds": 0.03})
    for k, x in enumerate(edit.get("extra_audio", [])):
        path = source / f"{x['clip']}.mp4"
        gain = max(-14, min(10, args.speech_lufs - 3 - lufs(path, x["in"], x["out"])))
        chain = (SPEAKER + "," if x.get("effect") == "speaker" else "") + f"volume={gain:.1f}dB"
        out = temp / f"x{k:02d}.wav"
        ff("-ss", x["in"], "-t", round(x["out"] - x["in"], 3), "-i", path, "-vn", "-af", chain, "-ar", 48000, "-ac", 2, out)
        tracks.append({"path": str(out), "role": "sfx", "volume": 1.0, "start_seconds": marks[x["at"]]["start"] + x.get("offset", 0)})
    for k, v in enumerate(edit.get("voiceover", [])):
        path = proj / v["file"]
        tempo = float(v.get("tempo", 1.0))
        out = temp / f"vo{k:02d}.wav"
        chain = (f"atempo={tempo}," if tempo != 1.0 else "")
        ff("-i", path, "-vn", "-af", chain + "anull", "-ar", 48000, "-ac", 2, out)
        gain = max(-14, min(10, args.speech_lufs - lufs(out, 0, 600)))
        final = temp / f"vo{k:02d}_g.wav"
        ff("-i", out, "-af", f"volume={gain:.1f}dB", final)
        tracks.append({"path": str(final), "role": "primary", "volume": 1.0,
                       "start_seconds": marks[v["at"]]["start"] + v.get("offset", 0), "fade_in_seconds": 0.02, "fade_out_seconds": 0.05})

    registry.discover()
    raw = temp / "mix_raw.wav"
    r = registry.get("audio_mixer").execute({"operation": "mix", "tracks": tracks, "output_path": str(raw), "normalize": False})
    if not r.success:
        sys.exit(f"audio_mixer failed: {r.error}")
    # Two-pass loudness: measure the whole mix, then one static gain plus a peak limiter. Single-pass
    # loudnorm undershoots when narration has pauses and a silent tail (measured -17 LUFS for a -15.5 target).
    gain = args.target_lufs - lufs(raw, 0, total)
    # Limit at about -2 dBFS: AAC encoding adds a few tenths of a dB, and the delivery check is peak <= -1.5 dBFS.
    ff("-i", raw, "-af", f"apad,atrim=0:{total},volume={gain:.2f}dB,alimiter=limit=0.79:level=false,aresample=48000",
       "-c:a", "pcm_s16le", assets / "mix.wav")
    print(f"mix loudness: {lufs(assets / 'mix.wav', 0, total):.1f} LUFS (target {args.target_lufs})", file=sys.stderr)
    for f in temp.glob("*.wav"):
        f.unlink()
    temp.rmdir()

    def fill(body):
        def repl(m):
            name, field, add = m[1], m[2], float(m[3] or 0)
            if name == "total":
                return str(total)
            mark = marks[name]
            value = {"start": mark["start"], "duration": mark["duration"], "end": mark["start"] + mark["duration"]}[field]
            return str(q(value + add))
        body = re.sub(r"\{\{total\}\}", str(total), body)
        return re.sub(r"\{\{([\w-]+)\.(start|duration|end)([+-][\d.]+)?\}\}", repl, body)

    def read_opt(key):
        return fill((edit_path.parent / edit[key]).read_text(encoding="utf-8")) if edit.get(key) else ""

    shots = "\n      ".join(f'<video id="v{k}" class="clip shot" src="assets/{p["name"]}" data-start="{p["start"]}" data-duration="{p["duration"]}" '
                           f'data-track-index="{k % 2}" muted playsinline></video>' for k, p in enumerate(pictures))
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>{args.project}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      body {{ margin: 0; background: #000; }}
      #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; font-family: "Segoe UI", Arial, sans-serif; }}
      .shot {{ position: absolute; inset: 0; width: {W}px; height: {H}px; object-fit: cover; z-index: 1; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-width="{W}" data-height="{H}" data-duration="{total}">
      {shots}
      <audio id="mix" src="assets/mix.wav" data-start="0" data-duration="{total}" data-track-index="10" data-volume="1"></audio>
      {read_opt("overlay_html")}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      {read_opt("overlay_js")}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""
    (ws / "index.html").write_text(html, encoding="utf-8")
    (ws / "timeline.json").write_text(json.dumps({"total": total, "marks": marks, "pictures": pictures}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total_seconds": total, "marks": marks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
