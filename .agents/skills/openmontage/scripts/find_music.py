#!/usr/bin/env python3
"""Find and download licensed background music through the public Openverse API.

No API key is needed. The default license is CC0, which needs no attribution.

Search:
  python find_music.py search "corporate" "cinematic buildup" --min 28 --max 180

Download one result by Openverse id:
  python find_music.py download <id> --output projects/<id>/assets/music/bgm.mp3

The download writes <output>.license.json with the title, creator, license and
source page, so the rights record stays beside the audio file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.openverse.org/v1/audio/"
AGENT = "openmontage-music-finder/1.0"
ALLOWED_HOSTS = ("cdn.freesound.org", "freesound.org", "upload.wikimedia.org", "prod-1.storage.jamendo.com")
MAX_BYTES = 40 * 1024 * 1024
NOT_MUSIC = re.compile(r"\b(noise|noises|sfx|explosion|siren|sirens|rumble|footsteps?|anthem|voice|vocal|drone)\b", re.I)


def get_json(url: str) -> dict:
    with urlopen(Request(url, headers={"User-Agent": AGENT}), timeout=30) as response:
        return json.load(response)


def search(queries: list[str], licenses: str, min_s: float, max_s: float, limit: int) -> list[dict]:
    found: dict[str, dict] = {}
    for query in queries:
        params = urlencode({"q": query, "license": licenses, "page_size": 20})
        for item in get_json(f"{API}?{params}").get("results", []):
            seconds = (item.get("duration") or 0) / 1000
            title = item.get("title") or ""
            if not min_s <= seconds <= max_s or NOT_MUSIC.search(title):
                continue
            found.setdefault(item["id"], {
                "id": item["id"], "title": title, "creator": item.get("creator"),
                "seconds": round(seconds), "license": item.get("license"),
                "source": item.get("source"), "page": item.get("foreign_landing_url"),
                "matched_query": query,
            })
    return list(found.values())[:limit]


def download(item_id: str, output: Path) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", item_id):
        raise ValueError("Openverse ids are 36-character UUIDs")
    item = get_json(f"{API}{item_id}/")
    url = item.get("url") or ""
    host = re.sub(r"^https://([^/]+)/.*$", r"\1", url)
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"Audio host is not on the allowed list: {host or 'missing'}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(Request(url, headers={"User-Agent": AGENT}), timeout=60) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Audio file is larger than the 40 MB limit")
    output.write_bytes(data)

    record = {
        "title": item.get("title"), "creator": item.get("creator"),
        "license": item.get("license"), "license_version": item.get("license_version"),
        "license_url": item.get("license_url"), "attribution": item.get("attribution"),
        "source": item.get("source"), "page": item.get("foreign_landing_url"),
        "openverse_id": item_id, "audio_url": url, "bytes": len(data),
    }
    Path(f"{output}.license.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    find = commands.add_parser("search")
    find.add_argument("queries", nargs="+")
    find.add_argument("--license", default="cc0", help="Comma-separated Openverse licenses; default cc0")
    find.add_argument("--min", type=float, default=28)
    find.add_argument("--max", type=float, default=240)
    find.add_argument("--limit", type=int, default=25)
    get = commands.add_parser("download")
    get.add_argument("id")
    get.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        if args.command == "search":
            result = search(args.queries, args.license, args.min, args.max, args.limit)
        else:
            result = download(args.id, args.output)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"find_music failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
