#!/usr/bin/env python3
"""Render a 16:9 security-camera clip into a vertical (1080x1920) phone-app live-view screen.

Run from the OpenMontage repo with its venv:
  python -X utf8 <skill>/scripts/app_feed.py --project <id> --config app_feed.json

Config (paths relative to the project):
{
  "app": {"title": "Portón", "theme": "light", "talk_label": "Hablar", "live_label": "EN VIVO"},
  "clips": [
    {"id": "UI_S10", "src": "assets/video/S10.mp4", "in": 0.5, "out": 4.5, "clock": "10:24:31",
     "pan": {"zoom": 0.78, "from": 0.0, "to": 1.0, "start": 0.6, "end": 3.2}},
    {"id": "UI_S14", "src": "assets/video/S14.mp4", "in": 0, "out": 5, "clock": "10:25:02",
     "follow": {"zoom": 0.7}, "box": true},
    {"id": "UI_S12", "src": "assets/video/S12.mp4", "in": 0, "out": 3, "clock": "10:24:58",
     "box": true, "box_from": 1.4, "banner": {"text": "Persona detectada · Portón", "at": 0.3}}
  ]
}
Writes assets/video/<id>.mp4 (silent; the edit takes sound from elsewhere).

Why these moves are physically honest: a pan/tilt camera rotates about a fixed optical centre, so near and far
objects shift together with no parallax. Panning a crop window across a wider fixed shot reproduces exactly that.
"follow" moves the crop with the person found by background subtraction (the feed is a fixed camera), with a speed
limit so it turns like a motor, not like a hand. "box" draws the app's person-detection rectangle from the same
detection ("box_from": seconds before the box appears, like an app that locks on after a moment; use it when the
first frames' detection is unreliable). These are app-layer graphics and reframing; the generated pixels themselves are not edited.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
FEED_Y, FEED_H = 300, 608
FONT_DIR = Path("C:/Windows/Fonts")


def font(size, bold=False):
    for name in (["seguisb.ttf", "segoeuib.ttf"] if bold else []) + ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]:
        for base in (FONT_DIR, Path("/usr/share/fonts/truetype/dejavu")):
            if (base / name).exists():
                return ImageFont.truetype(str(base / name), size)
    return ImageFont.load_default()


def read_frames(src, t_in, t_out):
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    frames, i = [], 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        t = i / fps
        if t_in - 1e-6 <= t < t_out:
            frames.append(fr)
        i += 1
    cap.release()
    # resample to output fps
    n_out = max(1, int(round((t_out - t_in) * FPS)))
    idx = [min(len(frames) - 1, int(k * fps / FPS)) for k in range(n_out)]
    return [frames[j] for j in idx]


def detect_person(frames):
    """Largest moving blob per frame on a fixed camera. Returns list of (x, y, w, h) or None."""
    # The background is the per-pixel median of the clip: a walking person is gone from it, so the box is right
    # from the first frame (a learning subtractor needs warm-up and boxes the wrong spot at the start).
    sample = frames[:: max(1, len(frames) // 40)]
    bg = np.median(np.stack([cv2.GaussianBlur(f, (5, 5), 0) for f in sample]), axis=0).astype(np.uint8)
    boxes = []
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    hh, ww = frames[0].shape[:2]
    for fr in frames:
        diff = cv2.absdiff(cv2.GaussianBlur(fr, (5, 5), 0), bg).max(axis=2)
        m = (diff > 38).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        m = cv2.dilate(m, k, iterations=3)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cands = [cv2.boundingRect(c) for c in cnts if cv2.contourArea(c) > ww * hh * 0.002]
        cands = [b for b in cands if b[3] > b[2] * 0.9]  # upright: people are taller than wide
        if cands:
            # a reflection in a puddle also moves and sits right below the person: take the highest big blob
            big = max(b[2] * b[3] for b in cands)
            cands = [b for b in cands if b[2] * b[3] >= 0.3 * big]
            boxes.append(min(cands, key=lambda b: b[1] + b[3] / 2))
        else:
            boxes.append(None)
    # fill gaps and smooth
    last = None
    for i, b in enumerate(boxes):
        if b is None and last is not None:
            boxes[i] = last
        last = boxes[i] or last
    first = next((b for b in boxes if b), None)
    boxes = [b or first for b in boxes]
    if first is None:
        return [None] * len(frames)
    arr = np.array(boxes, dtype=float)
    # A person walking across a fixed view moves smoothly: fit each of centre-x, bottom-y, width, height with a
    # quadratic over time, drop frames far from the fit (blobs from puddles, shadows, partial bodies) and refit.
    t = np.arange(len(arr), dtype=float)
    feats = np.stack([arr[:, 0] + arr[:, 2] / 2, arr[:, 1] + arr[:, 3], arr[:, 2], arr[:, 3]], 1)
    keep = np.ones(len(arr), bool)
    for _ in range(3):
        if keep.sum() < 6:
            break
        fits = [np.polyval(np.polyfit(t[keep], feats[keep, j], 2), t) for j in range(4)]
        err = np.max([np.abs(feats[:, j] - fits[j]) / max(1.0, np.median(feats[keep, 3])) for j in range(4)], 0)
        keep = err < 0.35
    if keep.sum() >= 6:
        fits = np.stack([np.polyval(np.polyfit(t[keep], feats[keep, j], 2), t) for j in range(4)], 1)
        cx, bottom, bw, bh = fits.T
        arr = np.stack([cx - bw / 2, bottom - bh, bw, bh], 1)
    ker = np.ones(5) / 5
    sm = np.stack([np.convolve(np.pad(arr[:, j], 2, mode="edge"), ker, mode="valid") for j in range(4)], 1)
    return [tuple(r) for r in sm]


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def crop_windows(clip, frames, boxes):
    hh, ww = frames[0].shape[:2]
    n = len(frames)
    if "pan" in clip:
        p = clip["pan"]
        z = p.get("zoom", 0.78)
        cw, ch = ww * z, hh * z
        out = []
        for i in range(n):
            t = i / FPS
            u = ease((t - p.get("start", 0)) / max(0.01, p.get("end", n / FPS) - p.get("start", 0)))
            pos = p.get("from", 0) + (p.get("to", 1) - p.get("from", 0)) * u
            out.append(((ww - cw) * pos, (hh - ch) * p.get("y", 0.5), cw, ch))
        return out
    if "follow" in clip and boxes[0] is not None:
        z = clip["follow"].get("zoom", 0.72)
        cw, ch = ww * z, hh * z
        max_step = clip["follow"].get("deg_per_s", 0.35) * ww / FPS  # crop shift per frame
        cx = boxes[0][0] + boxes[0][2] / 2
        out = []
        for b in boxes:
            target = b[0] + b[2] / 2
            cx += max(-max_step, min(max_step, (target - cx) * 0.12))
            x = min(ww - cw, max(0, cx - cw / 2))
            out.append((x, (hh - ch) * clip["follow"].get("y", 0.5), cw, ch))
        return out
    z = clip.get("zoom", 1.0)
    cw, ch = ww * z, hh * z
    x = (ww - cw) * clip.get("x", 0.5)
    return [(x, (hh - ch) * clip.get("y", 0.5), cw, ch)] * n


def chrome(app, night=False):
    bg = (246, 247, 249)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # status bar
    d.text((60, 52), app.get("phone_time", "10:24"), fill=(20, 20, 20), font=font(40, True))
    for i in range(4):
        d.rectangle((880 + i * 18, 78 - i * 8, 892 + i * 18, 88), fill=(20, 20, 20))
    d.rounded_rectangle((970, 60, 1030, 88), 6, outline=(20, 20, 20), width=3)
    d.rectangle((974, 64, 1016, 84), fill=(20, 20, 20))
    # header
    d.text((50, 150), "‹", fill=(30, 30, 30), font=font(80))
    title = app.get("title", "Camera")
    tw = d.textlength(title, font=font(48, True))
    d.text(((W - tw) / 2, 168), title, fill=(25, 25, 25), font=font(48, True))
    d.ellipse((960, 182, 1000, 222), outline=(60, 60, 60), width=4)
    # controls under the feed
    y0 = FEED_Y + FEED_H + 70
    labels = [("rec", "Grabar"), ("snap", "Foto"), ("light", "Luz"), ("mute", "Audio")]
    for i, (_, lab) in enumerate(labels):
        cx = 135 + i * 270
        d.ellipse((cx - 62, y0, cx + 62, y0 + 124), fill=(255, 255, 255), outline=(220, 222, 226), width=3)
        lw = d.textlength(lab, font=font(34))
        d.text((cx - lw / 2, y0 + 140), lab, fill=(90, 90, 95), font=font(34))
    # icons (simple)
    d.ellipse((135 - 20, y0 + 42, 135 + 20, y0 + 82), fill=(225, 50, 50))
    d.rounded_rectangle((405 - 30, y0 + 40, 405 + 30, y0 + 84), 8, outline=(50, 50, 55), width=5)
    d.ellipse((405 - 11, y0 + 51, 405 + 11, y0 + 73), outline=(50, 50, 55), width=5)
    d.polygon([(675 - 14, y0 + 36), (675 + 16, y0 + 36), (675 + 4, y0 + 62), (675 + 20, y0 + 62), (675 - 12, y0 + 92),
               (675 - 2, y0 + 68), (675 - 18, y0 + 68)], fill=(50, 50, 55))
    d.polygon([(945 - 26, y0 + 50), (945 - 8, y0 + 50), (945 + 14, y0 + 32), (945 + 14, y0 + 92), (945 - 8, y0 + 74),
               (945 - 26, y0 + 74)], fill=(50, 50, 55))
    # PTZ pad + talk button
    py = y0 + 300
    d.ellipse((W / 2 - 230, py, W / 2 + 230, py + 460), fill=(255, 255, 255), outline=(222, 224, 228), width=3)
    for dx, dy, ang in ((0, -170, 0), (0, 170, 180), (-170, 0, 270), (170, 0, 90)):
        cx, cy = W / 2 + dx, py + 230 + dy
        tri = np.array([(0, -22), (-20, 12), (20, 12)], dtype=float)
        r = np.deg2rad(ang)
        rot = tri @ np.array([[np.cos(r), np.sin(r)], [-np.sin(r), np.cos(r)]])
        d.polygon([(cx + x, cy + y) for x, y in rot], fill=(120, 122, 128))
    d.ellipse((W / 2 - 95, py + 135, W / 2 + 95, py + 325), fill=(30, 136, 229))
    d.rounded_rectangle((W / 2 - 18, py + 175, W / 2 + 18, py + 240), 18, fill="white")
    d.arc((W / 2 - 36, py + 200, W / 2 + 36, py + 268), 0, 180, fill="white", width=6)
    d.line((W / 2, py + 268, W / 2, py + 288), fill="white", width=6)
    lab = app.get("talk_label", "Talk")
    lw = d.textlength(lab, font=font(36))
    d.text((W / 2 - lw / 2, py + 480), lab, fill=(90, 90, 95), font=font(36))
    return img


def render(proj, app, clip):
    src = proj / clip["src"]
    frames = read_frames(src, clip.get("in", 0), clip["out"])
    boxes = detect_person(frames) if (clip.get("box") or "follow" in clip) else [None] * len(frames)
    wins = crop_windows(clip, frames, boxes)
    # the phone status-bar time follows the feed clock unless set, so a night clip never shows a daytime time
    base = chrome({**app, "phone_time": clip.get("phone_time", clip.get("clock", app.get("phone_time", "10:24"))[:5])},
                  clip.get("night", False))
    out = proj / "assets/video" / f"{clip['id']}.mp4"
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17",
                             str(out)], stdin=subprocess.PIPE)
    hh, ww = frames[0].shape[:2]
    h0, m0, s0 = (int(x) for x in clip.get("clock", "10:24:31").split(":"))
    banner = clip.get("banner")
    for i, (fr, win, b) in enumerate(zip(frames, wins, boxes)):
        x, y, cw, ch = win
        crop = fr[int(y):int(y + ch), int(x):int(x + cw)]
        feed = cv2.resize(crop, (W, FEED_H), interpolation=cv2.INTER_CUBIC)
        feed = Image.fromarray(cv2.cvtColor(feed, cv2.COLOR_BGR2RGB))
        fd = ImageDraw.Draw(feed)
        if clip.get("box") and b is not None and i / FPS >= clip.get("box_from", 0):
            sx, sy = W / cw, FEED_H / ch
            bx, by, bw, bh = (b[0] - x) * sx, (b[1] - y) * sy, b[2] * sx, b[3] * sy
            pad = 10
            fd.rectangle((bx - pad, by - pad, bx + bw + pad, by + bh + pad), outline=(255, 214, 0), width=5)
        sec = s0 + int(i / FPS)
        stamp = f"{h0:02d}:{m0 + sec // 60:02d}:{sec % 60:02d}"
        fd.text((24, 18), clip.get("date", "2026-11-12") + "  " + stamp, fill="white", font=font(30, True),
                stroke_width=2, stroke_fill=(0, 0, 0))
        fd.ellipse((W - 150, 26, W - 132, 44), fill=(235, 40, 40))
        fd.text((W - 122, 16), app.get("live_label", "LIVE"), fill="white", font=font(28, True), stroke_width=2, stroke_fill=(0, 0, 0))
        img = base.copy()
        img.paste(feed, (0, FEED_Y))
        if banner and i / FPS >= banner.get("at", 0):
            u = ease((i / FPS - banner.get("at", 0)) / 0.25)
            by0 = int(-200 + u * 240)
            dd = ImageDraw.Draw(img)
            dd.rounded_rectangle((30, by0, W - 30, by0 + 170), 34, fill=(255, 255, 255), outline=(225, 225, 230), width=2)
            dd.rounded_rectangle((60, by0 + 40, 150, by0 + 130), 20, fill=(30, 136, 229))
            dd.ellipse((88, by0 + 62, 122, by0 + 96), outline="white", width=6)
            dd.text((180, by0 + 32), app.get("app_name", "Cámara Solar"), fill=(120, 120, 125), font=font(30))
            dd.text((180, by0 + 78), banner["text"], fill=(20, 20, 20), font=font(40, True))
        proc.stdin.write(np.asarray(img, dtype=np.uint8).tobytes())
    proc.stdin.close()
    proc.wait()
    found = sum(1 for b in boxes if b is not None)
    print(f"{out}  {len(frames) / FPS:.2f}s  person boxes: {found}/{len(frames)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    proj = Path("projects") / a.project
    cfg = json.loads((proj / a.config).read_text(encoding="utf-8"))
    for clip in cfg["clips"]:
        if a.only and clip["id"] not in a.only:
            continue
        render(proj, cfg.get("app", {}), clip)


if __name__ == "__main__":
    sys.exit(main())
