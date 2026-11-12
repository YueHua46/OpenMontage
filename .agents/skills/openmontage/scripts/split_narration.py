#!/usr/bin/env python3
"""Split one narration take into per-line files at the pauses between sentences.

Generate the whole narration in ONE request with the locked voice (same speaker and tone throughout),
then split it here so each line can be placed on its shot.

  python -X utf8 <S>/split_narration.py --audio projects/<id>/assets/audio/vo_full.mp3 \
         --lines projects/<id>/edit/vo_lines.json --out projects/<id>/assets/audio

vo_lines.json: {"lines": [{"id": "vo1", "text": "..."}, ...]}, in reading order.
Method: count sentences per line; detect pauses; keep the longest (sentences - 1) pauses as
sentence ends (shorter ones are commas); cut after the last sentence of each line. Prints the cut
times and each line's first/last words so the result can be checked; exits 1 if the counts do not fit.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def probe(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True).stdout)


def pauses(path, noise_db, min_len):
    """Silences between words as (middle, length); leading and trailing silence excluded."""
    dur = probe(path)
    log = subprocess.run(["ffmpeg", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    s = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    e = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    e += [dur] * (len(s) - len(e))  # a silence running to the end has no silence_end line
    return [((a + b) / 2, b - a) for a, b in zip(s, e) if a > 0.05 and b < dur - 0.05]


def spoken_units(text):
    """Rough spoken length: vowel groups per word; all-caps or digit tokens are read letter by letter."""
    n = 0
    for tok in re.findall(r"[A-Za-z0-9']+", text.replace("-", " ")):
        if tok.isupper() or any(ch.isdigit() for ch in tok):
            n += 1.3 * len(tok)  # "HD" = aitch-dee, "2K" = two-kay
        else:
            n += max(1, len(re.findall(r"[aeiouy]+", tok.lower())))
    return n


def split_sentences(text):
    return [x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x] or [text]


def pick_ends(pauses_, expected, bonus=0.6):
    """Choose one pause per expected sentence end, in order, minimising |time - expected| - bonus * length."""
    n, k = len(pauses_), len(expected)
    inf = float("inf")
    best = [[inf] * (n + 1) for _ in range(k + 1)]
    back = [[None] * (n + 1) for _ in range(k + 1)]
    for j in range(n + 1):
        best[0][j] = 0.0
    for i in range(1, k + 1):
        for j in range(i, n + 1):
            skip = best[i][j - 1]
            take = best[i - 1][j - 1] + abs(pauses_[j - 1][0] - expected[i - 1]) - bonus * pauses_[j - 1][1]
            best[i][j], back[i][j] = (take, True) if take <= skip else (skip, False)
    out, i, j = [], k, n
    while i > 0:
        if back[i][j]:
            out.append(pauses_[j - 1]); i -= 1
        j -= 1
    return out[::-1]


def norm(word):
    return re.sub(r"[^a-z0-9]", "", word.lower())


def asr_cuts(audio, lines, dur):
    """Cut times from word timestamps (faster-whisper), aligned to the script words. None if unavailable."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    import difflib

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(audio), word_timestamps=True)
    heard = [(norm(w.word), w.start, w.end) for s in segs for w in (s.words or []) if norm(w.word)]
    script, owner = [], []
    for i, line in enumerate(lines):
        for w in line["text"].replace("-", " ").split():
            if norm(w):
                script.append(norm(w)); owner.append(i)
    match = {}
    for a, b, size in difflib.SequenceMatcher(None, script, [h[0] for h in heard], autojunk=False).get_matching_blocks():
        for k in range(size):
            match[a + k] = b + k
    cuts = [0.0]
    for i in range(len(lines) - 1):
        last = max((k for k in match if owner[k] == i), default=None)
        first = min((k for k in match if owner[k] == i + 1), default=None)
        if last is None or first is None:
            return None
        cuts.append(float(heard[match[last]][2] + heard[match[first]][1]) / 2)
    return cuts + [dur]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--lines", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--noise-db", type=float, default=-35)
    ap.add_argument("--min-pause", type=float, default=0.15)
    args = ap.parse_args()

    lines = json.loads(Path(args.lines).read_text(encoding="utf-8"))["lines"]
    dur = probe(args.audio)
    cuts = asr_cuts(args.audio, lines, dur)
    if cuts:
        print("method: word timestamps (faster-whisper)")
        return write(args, lines, cuts, [])
    print("method: pauses (faster-whisper not installed; doctor.py --fix installs it)")
    inner = pauses(args.audio, args.noise_db, args.min_pause)
    sents = [s for l in lines for s in split_sentences(l["text"])]
    counts = [len(split_sentences(l["text"])) for l in lines]
    need = len(sents) - 1
    if len(inner) < need:
        sys.exit(f"found {len(inner)} pauses, need {need} sentence ends; lower --noise-db or --min-pause and check by ear")
    # Expected end of each sentence from its share of the spoken syllables; the speaking rate is roughly even.
    chars = [spoken_units(s) for s in sents]
    total, acc_c, expected = sum(chars), 0, []
    for c in chars[:-1]:
        acc_c += c
        expected.append(dur * acc_c / total)
    ends = pick_ends(inner, expected)
    cuts, acc = [0.0], 0
    for c in counts[:-1]:
        acc += c
        cuts.append(ends[acc - 1][0])
    cuts.append(dur)
    write(args, lines, cuts, [round(p[0], 2) for p in inner if p not in ends])


def write(args, lines, cuts, dropped):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    trim = ("silenceremove=start_periods=1:start_threshold=-40dB,areverse,"
            "silenceremove=start_periods=1:start_threshold=-40dB,areverse")
    print("cuts:", [round(c, 2) for c in cuts], "| dropped short pauses:", dropped)
    for i, line in enumerate(lines):
        f = out / f"{line['id']}.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.audio, "-ss", f"{cuts[i]:.3f}", "-to", f"{cuts[i + 1]:.3f}",
                        "-af", trim, "-c:a", "libmp3lame", "-q:a", "2", str(f)], check=True)
        words = line["text"].split()
        print(f"{line['id']}: {cuts[i]:.2f}-{cuts[i + 1]:.2f} s ({probe(f):.2f} s) \"{' '.join(words[:3])} ... {' '.join(words[-3:])}\"")


if __name__ == "__main__":
    main()
