#!/usr/bin/env python3
"""Fetch the latest stable + pre-release for each Cinefin repo and write
_data/releases.json, so the download page can render from baked-in data
instead of calling the GitHub API from the browser.

Run at build time (see .github/workflows/pages.yml). Set GITHUB_TOKEN in the
environment to lift the API rate limit (Actions provides one automatically).
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPOS = {
    "server": "cinefin/cinefin",
    "agent": "cinefin/cinefin-playout",
}

# Repos whose per-platform binary assets we surface. The server leads with
# Docker but also ships native installers; the agent is binaries-only.
WITH_BINARIES = {"agent", "server"}

OUT = os.path.join(os.path.dirname(__file__), os.pardir, "_data", "releases.json")


def classify(name):
    n = name.lower()
    # Explicit OS name wins over extension (amd64/arm64 are architectures).
    if re.search(r"(^|[-_.])(win|win32|win64|windows)([-_.]|$)", n):
        return "windows"
    if re.search(r"(^|[-_.])(linux)([-_.]|$)", n):
        return "linux"
    if re.search(r"\.(exe|msi)$", n):
        return "windows"
    if re.search(r"\.(appimage|deb|rpm|flatpak|snap)$", n) or re.search(r"\.tar\.(gz|xz|bz2|zst)$", n):
        return "linux"
    if re.search(r"\.zip$", n):
        return "windows"
    return "other"


def fmt_size(b):
    if not b:
        return ""
    mb = b / 1048576
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    if mb >= 10:
        return f"{round(mb)} MB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{max(1, round(b / 1024))} KB"


def fmt_date(iso):
    if not iso:
        return ""
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    # Avoid %-d (not portable): build "Sep 5, 2026" by hand.
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def api(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{path}", headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "cinefin-site-build",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch(repo):
    return api(f"{repo}/releases?per_page=30")


def resolve_commit(repo, tag):
    """The precise version behind a rolling tag (e.g. `edge`) is its commit."""
    if not tag:
        return ""
    try:
        commit = api(f"{repo}/commits/{tag}")
        sha = commit.get("sha") or ""
        return sha[:7]
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"warning: could not resolve {repo}@{tag}: {e}", file=sys.stderr)
        return ""


def channel_meta(rel):
    return {"tag": rel.get("tag_name") or "latest", "date": fmt_date(rel.get("published_at"))}


def channel_binaries(rel):
    data = channel_meta(rel)
    groups = {"linux": [], "windows": []}
    for a in rel.get("assets", []):
        url = a.get("browser_download_url")
        name = a.get("name", "")
        if not url:
            continue
        key = classify(name)
        if key in groups:
            groups[key].append({
                "name": name,
                "url": url,
                "size": fmt_size(a.get("size", 0)),
                "variant": "no MPV" if re.search(r"no[-_]?mpv", name.lower()) else "",
            })
    data["linux"] = groups["linux"]
    data["windows"] = groups["windows"]
    return data


def pick(rels):
    stable = next((r for r in rels if not r.get("prerelease") and not r.get("draft")), None)
    pre = next((r for r in rels if r.get("prerelease") and not r.get("draft")), None)
    return stable, pre


def build_product(key, repo):
    result = {"stable": None, "pre": None}
    try:
        rels = fetch(repo)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"warning: could not fetch {repo}: {e}", file=sys.stderr)
        result["error"] = str(e)
        return result
    if not isinstance(rels, list):
        print(f"warning: unexpected response for {repo}", file=sys.stderr)
        return result
    stable, pre = pick(rels)
    shape = channel_binaries if key in WITH_BINARIES else channel_meta
    for name, rel in (("stable", stable), ("pre", pre)):
        if not rel:
            continue
        entry = shape(rel)
        entry["commit"] = resolve_commit(repo, rel.get("tag_name"))
        result[name] = entry
    return result


def main():
    data = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for key, repo in REPOS.items():
        data[key] = build_product(key, repo)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"wrote {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
