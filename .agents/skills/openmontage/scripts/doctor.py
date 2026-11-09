#!/usr/bin/env python3
"""Check that this machine can run the OpenMontage skill. Never prints key values.

Usage: python doctor.py [--repo PATH] [--full]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REQUIRED_KEYS = {
    "TSTSTS_MEDIA_API_KEY": "images (gpt-image-2.6)",
    "OMNIAKEY_API_KEY": "video (MiniMax H3)",
    "FISH_AUDIO_API_KEY": "voiceover (Fish Audio)",
}
OPTIONAL_KEYS = {"TSTSTS_TEXT_API_KEY": "text helpers"}


def find_repo(arg):
    here = Path(__file__).resolve().parents[4]
    for raw in (arg, os.environ.get("OPENMONTAGE_HOME"), here, Path.home() / "Documents/projects/OpenMontage", Path.cwd()):
        if raw and (Path(raw) / "AGENT_GUIDE.md").exists():
            return Path(raw).resolve()
    return None


def venv_python(repo):
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        if (repo / rel).exists():
            return repo / rel
    return None


def run(cmd, cwd=None, timeout=120):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--full", action="store_true", help="also run the HyperFrames runtime check")
    args = ap.parse_args()
    failed = []

    def report(ok, label, fix=""):
        print(f"[{'OK' if ok else 'FAIL'}] {label}" + ("" if ok or not fix else f"  -> {fix}"))
        if not ok:
            failed.append(label)

    repo = find_repo(args.repo)
    report(bool(repo), f"OpenMontage repo: {repo}", "run the team installer, or set OPENMONTAGE_HOME")
    if not repo:
        return 1

    py = venv_python(repo)
    report(bool(py), f"Repo virtualenv: {py}", "python -m venv .venv, then pip install -r requirements.txt")

    for tool in ("ffmpeg", "ffprobe", "git"):
        report(bool(shutil.which(tool)), f"{tool} on PATH", f"install {tool}")
    npx = shutil.which("npx")
    node = shutil.which("node")
    report(bool(npx), "npx on PATH", "install Node.js 22 or newer")
    if node:
        _, out, _ = run([node, "--version"])
        m = re.match(r"v(\d+)", out.strip())
        report(bool(m) and int(m[1]) >= 22, f"Node.js >= 22 ({out.strip()})", "HyperFrames needs Node.js 22 or newer")
    else:
        report(False, "Node.js on PATH", "install Node.js 22 or newer")

    env = {}
    env_file = repo / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)", line)
            if m:
                env[m[1]] = m[2].strip().strip("\"'")
    for name, purpose in REQUIRED_KEYS.items():
        report(bool(env.get(name) or os.environ.get(name)), f"{name} is set, for {purpose}", f"add it to {env_file}; never paste it into a chat")
    for name, purpose in OPTIONAL_KEYS.items():
        print(f"[{'OK' if env.get(name) or os.environ.get(name) else 'INFO'}] {name} ({purpose}, optional)")

    relay_file = env.get("OPENMONTAGE_RELAY_CONFIG") or os.environ.get("OPENMONTAGE_RELAY_CONFIG") or "relay_providers.yaml"
    has_config = (repo / relay_file).exists()
    report(has_config, f"relay config present: {relay_file}", "set OPENMONTAGE_RELAY_CONFIG=relay_providers.team.yaml in .env")
    if py and has_config:
        code, out, err = run([str(py), "-X", "utf8", "-m", "tools.relay", "check"], cwd=repo)
        try:
            data = json.loads(out)
            report(bool(data.get("ok")), "relay config valid", "; ".join(map(str, data.get("errors", []))) or err[-200:])
            tools = {m.get("tool") for m in data.get("models", [])}
            for kind, tool in (data.get("default_tools") or {}).items():
                report(tool in tools, f"default {kind} tool registered: {tool}", "run update.cmd to get the latest code")
            for name, relay in (data.get("relays") or {}).items():
                if name in ("tststs", "omniakey") and not relay.get("live"):
                    report(False, f"relay {name} is live", f"set live: true in {relay_file}")
        except ValueError:
            report(False, "relay check ran", (err or out)[-300:])

    try:
        urllib.request.urlopen(urllib.request.Request("https://api.omniakey.com/", headers={"User-Agent": "openmontage-doctor"}), timeout=15)
        online = True
    except urllib.error.HTTPError:
        online = True
    except OSError:
        online = False
    report(online, "Network reaches the video service", "in Codex, restart with codex-video.cmd from the repo; otherwise check the proxy")

    if args.full and py:
        code, out, err = run([str(py), "-X", "utf8", "-c",
                              "from tools.tool_registry import registry; registry.discover(); r=registry.get('hyperframes_compose').execute({'operation':'doctor'}); print(r.success, r.error)"],
                             cwd=repo, timeout=300)
        report(code == 0 and out.strip().splitlines()[-1:] and out.strip().splitlines()[-1].startswith("True"), "HyperFrames runtime", (out or err)[-300:])

    print("\nREADY" if not failed else f"\nNOT READY: {len(failed)} problem(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
