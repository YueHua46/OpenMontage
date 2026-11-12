#!/usr/bin/env python3
"""Rough check that narration files come from one voice: median pitch (F0) per file.

The agent cannot hear. Without a fixed voice id, TTS "default voice" can change speaker on every
request (a real run gave 225, 102, 165, 148 and 190 Hz for five lines of one ad). Pitch alone does
not prove the same speaker, but a large spread proves different ones.

  python -X utf8 <S>/voice_check.py projects/<id>/assets/audio/*.mp3 [--tolerance 0.2]

Exit code 1 when any file's median F0 is more than `tolerance` away from the overall median.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np


def median_f0(path, fs=16000, frame=640):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(fs), "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(float) / 32768
    lo, hi = fs // 400, fs // 70  # 70-400 Hz covers adult speech
    vals = []
    for i in range(0, len(x) - frame, frame // 2):
        fr = x[i:i + frame]
        if np.sqrt((fr ** 2).mean()) < 0.02:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[frame - 1:]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.3 * ac[0]:
            vals.append(fs / k)
    return float(np.median(vals)) if vals else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--tolerance", type=float, default=0.2, help="allowed relative distance from the overall median")
    args = ap.parse_args()
    rows = [(Path(f).name, median_f0(f)) for f in args.files]
    found = [v for _, v in rows if v]
    if not found:
        sys.exit("no voiced audio found")
    ref = float(np.median(found))
    bad = []
    for name, v in rows:
        off = None if v is None else (v - ref) / ref
        flag = "" if off is not None and abs(off) <= args.tolerance else "  <-- different voice?"
        if flag:
            bad.append(name)
        print(f"{name}: median F0 {v and round(v)} Hz" + (f" ({off:+.0%} from {round(ref)} Hz)" if off is not None else "") + flag)
    if bad:
        print("Pitch spread suggests more than one speaker. Use one fixed voice id and generate the narration in one request.")
        sys.exit(1)
    print("Pitch is consistent (not proof of one speaker; the user still listens).")


if __name__ == "__main__":
    main()
