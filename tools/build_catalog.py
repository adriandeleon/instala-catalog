#!/usr/bin/env python3
"""Build catalog.json and CONFORMANCE.md from the apps' GitHub releases.

Reads registry.yml for the list of repositories, takes each app's .instala.yml from the repository at
its release tag (or the copy staged in apps/ until the repository has its own), and turns the latest
release into catalog entries as SPEC.md describes. Files that do not follow the naming rule are left
out of the catalog and reported in CONFORMANCE.md instead.

    python3 tools/build_catalog.py [--out DIR]

Needs PyYAML, and jsonschema to validate. A token in GITHUB_TOKEN or GH_TOKEN (or a signed-in `gh`)
lifts the API rate limit; public repositories work without one for a few runs an hour.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"

OSES = ("macos", "windows", "linux")
ARCHES = ("x64", "arm64")
KINDS = ("dmg", "msi", "zip", "tar.gz", "AppImage", "deb", "rpm")
CHECKSUMS = "checksums_sha256.txt"

# What a release must carry (SPEC 1.3). Recommended targets are reported but never fail a release.
REQUIRED = [("macos", "arm64", "dmg"), ("macos", "x64", "dmg"), ("windows", "x64", "msi"), ("linux", "x64", "tar.gz")]
RECOMMENDED = [("linux", "arm64", "tar.gz")]

# Attached files the catalog has no use for, and which are therefore not a conformance problem.
EXTRAS = re.compile(r"(\.jar|\.md|-signing-key\.asc|^checksums[^/]*\.txt)$", re.IGNORECASE)


# --- GitHub ------------------------------------------------------------------------------------------

def token() -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class GitHub:
    def __init__(self, tok: str | None):
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if tok:
            self.headers["Authorization"] = f"Bearer {tok}"

    def get(self, path: str):
        """GET an API path as JSON; None when it does not exist."""
        req = urllib.request.Request(f"{API}/{path}", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise SystemExit(f"GitHub API {path}: HTTP {e.code} {e.read()[:200]!r}")

    def file(self, owner: str, repo: str, path: str, ref: str) -> bytes | None:
        q = urllib.parse.quote(path)
        data = self.get(f"repos/{owner}/{repo}/contents/{q}?ref={urllib.parse.quote(ref)}")
        if not data or isinstance(data, list) or data.get("type") != "file":
            return None
        return base64.b64decode(data["content"]) if data.get("content") else b""

    def exists(self, owner: str, repo: str, path: str, ref: str) -> bool:
        q = urllib.parse.quote(path)
        data = self.get(f"repos/{owner}/{repo}/contents/{q}?ref={urllib.parse.quote(ref)}")
        return bool(data) and not isinstance(data, list)

    def text(self, url: str) -> str:
        req = urllib.request.Request(url, headers={k: v for k, v in self.headers.items() if k == "Authorization"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")


# --- Building one app --------------------------------------------------------------------------------

class Report:
    """Conformance findings for one app: 'required' breaks a rule, 'recommended' and 'note' do not."""

    def __init__(self, repo: str):
        self.repo = repo
        self.items: list[tuple[str, str]] = []

    def add(self, level: str, text: str):
        self.items.append((level, text))

    def count(self, level: str) -> int:
        return sum(1 for lv, _ in self.items if lv == level)


def asset_pattern(name: str, version: str) -> re.Pattern:
    kinds = "|".join(re.escape(k) for k in KINDS)
    return re.compile(
        rf"^{re.escape(name)}-{re.escape(version)}-(?P<os>{'|'.join(OSES)})-(?P<arch>{'|'.join(ARCHES)})\.(?P<kind>{kinds})$"
    )


def load_manifest(gh: GitHub, owner: str, repo: str, ref: str | None, rep: Report, validator) -> dict | None:
    raw = gh.file(owner, repo, ".instala.yml", ref) if ref else None
    if raw is not None:
        source = f"`.instala.yml` at `{ref}`"
    else:
        staged = ROOT / "apps" / f"{repo}.instala.yml"
        if not staged.exists():
            rep.add("required", "No `.instala.yml` in the repository and none staged in `apps/`. Left out of the catalog.")
            return None
        raw = staged.read_bytes()
        source = f"`apps/{repo}.instala.yml` (staged here)"
        rep.add("note", "Manifest is staged in the catalog repository. Commit it to the app as `.instala.yml`.")
    manifest = yaml.safe_load(raw) or {}
    if validator:
        errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
        for e in errors:
            where = "/".join(str(p) for p in e.path) or "(root)"
            rep.add("required", f"Manifest {source}: `{where}` {e.message}")
        if errors:
            return None
    return manifest


def build_release(gh: GitHub, owner: str, repo: str, rel: dict, name: str, rep: Report, check: bool) -> dict:
    version = rel["tag_name"].removeprefix("v")
    pattern = asset_pattern(name, version)
    by_name = {a["name"]: a for a in rel["assets"]}

    checksums = by_name.get(CHECKSUMS)
    sums: dict[str, str] = {}
    if check:
        legacy = [n for n in by_name if n.lower().startswith("checksums") and n != CHECKSUMS]
        if not checksums:
            if legacy:
                rep.add("required", f"Checksum file is `{legacy[0]}`. Rename it to `{CHECKSUMS}`.")
            else:
                rep.add("required", f"No `{CHECKSUMS}` attached.")
    # GitHub reports a sha256 digest for every asset; the checksum file covers releases from before that.
    if any(not (a.get("digest") or "").startswith("sha256:") for a in rel["assets"]):
        src = checksums or next((by_name[n] for n in by_name if n.lower().startswith("checksums")), None)
        if src:
            for line in gh.text(src["browser_download_url"]).splitlines():
                parts = line.split()
                if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                    sums[parts[1].lstrip("*")] = parts[0]

    assets, seen, odd = [], set(), []
    for a in rel["assets"]:
        n = a["name"]
        if n.endswith(".asc") and n[:-4] in by_name:
            continue
        m = pattern.match(n)
        if not m:
            if not EXTRAS.search(n):
                odd.append(n)
            continue
        digest = (a.get("digest") or "").removeprefix("sha256:") or sums.get(n)
        if not digest:
            rep.add("required", f"`{n}`: no SHA-256 from GitHub or the checksum file. Left out.")
            continue
        sig = by_name.get(n + ".asc")
        assets.append({
            "os": m["os"], "arch": m["arch"], "kind": m["kind"], "name": n,
            "url": a["browser_download_url"], "size": a["size"], "sha256": digest,
            "signatureUrl": sig["browser_download_url"] if sig else None,
        })
        seen.add((m["os"], m["arch"], m["kind"]))

    if check:
        for n in odd:
            rep.add("required", f"`{n}` does not match `{name}-{version}-<os>-<arch>.<ext>`. Left out.")
        for target in REQUIRED:
            if target not in seen:
                rep.add("required", f"Missing `{target[0]}-{target[1]}` `.{target[2]}`.")
        for target in RECOMMENDED:
            if target not in seen:
                rep.add("recommended", f"No `{target[0]}-{target[1]}` `.{target[2]}`.")

    order = {k: i for i, k in enumerate(OSES)}
    assets.sort(key=lambda x: (order[x["os"]], ARCHES.index(x["arch"]), KINDS.index(x["kind"])))
    return {
        "version": version,
        "tag": rel["tag_name"],
        "publishedAt": rel["published_at"],
        "notesUrl": rel["html_url"],
        "checksumsUrl": checksums["browser_download_url"] if checksums else None,
        "assets": assets,
    }


def build_app(gh: GitHub, owner: str, repo: str, validator) -> tuple[dict | None, Report]:
    rep = Report(repo)
    meta = gh.get(f"repos/{owner}/{repo}")
    if not meta:
        rep.add("required", "Repository not found.")
        return None, rep
    releases = [r for r in (gh.get(f"repos/{owner}/{repo}/releases?per_page=30") or []) if not r["draft"]]
    stable = next((r for r in releases if not r["prerelease"]), None)
    pre = next((r for r in releases if r["prerelease"]), None)
    if pre and stable and pre["published_at"] <= stable["published_at"]:
        pre = None

    ref = (stable or pre or {}).get("tag_name") or meta["default_branch"]
    manifest = load_manifest(gh, owner, repo, ref, rep, validator)
    if manifest is None:
        return None, rep
    name = manifest["name"]

    def raw(path: str) -> str:
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{urllib.parse.quote(ref)}/{urllib.parse.quote(path)}"

    def media(path: str | None, what: str, default: bool) -> str | None:
        if not path:
            return None
        if gh.exists(owner, repo, path, ref):
            return raw(path)
        rep.add("recommended" if default else "required", f"{what} `{path}` not found at `{ref}`.")
        return None

    icon = media(manifest.get("icon") or f"branding/{manifest['id']}-icon.svg", "Icon", "icon" not in manifest)
    icon_png = media(manifest.get("iconPng"), "PNG icon", False)
    if not manifest.get("iconPng"):
        rep.add("recommended", "No `iconPng`. JavaFX cannot draw SVG, so Instala needs a 256 px PNG.")
    shots = []
    for s in manifest.get("screenshots") or [{"path": "docs/screenshot.png"}]:
        url = media(s["path"], "Screenshot", "screenshots" not in manifest)
        if url:
            shots.append({"url": url, "alt": s.get("alt")})
    if shots and any(not s["alt"] for s in shots):
        rep.add("note", "A screenshot has no `alt` text.")

    if not stable and not pre:
        rep.add("required" if manifest["status"] != "preview" else "note", "No release yet.")
    repo_url = meta["html_url"]
    app = {
        "id": manifest["id"],
        "name": name,
        "tagline": manifest["tagline"],
        "category": manifest["category"],
        "status": manifest["status"],
        "color": manifest.get("color"),
        "keywords": manifest.get("keywords", []),
        "icon": icon,
        "iconPng": icon_png,
        "screenshots": shots,
        "links": {
            "homepage": manifest.get("homepage") or repo_url,
            "repository": repo_url,
            "releases": f"{repo_url}/releases",
            "issues": f"{repo_url}/issues",
        },
        "requires": manifest.get("requires", []),
        "packages": manifest.get("packages") or {},
        "release": build_release(gh, owner, repo, stable, name, rep, True) if stable else None,
        "preview": build_release(gh, owner, repo, pre, name, rep, not stable) if pre else None,
    }
    return app, rep


# --- Output ------------------------------------------------------------------------------------------

def conformance(reports: list[Report], apps: dict[str, dict], when: str) -> str:
    marks = {"required": "✗", "recommended": "△", "note": "·"}
    lines = [
        "# Release conformance",
        "",
        f"Generated {when} by `tools/build_catalog.py`. Rules: `SPEC.md`.",
        "",
        "✗ breaks a rule (the file or app is left out, or a required file is missing) · △ recommended · · note",
        "",
        "| App | Release | ✗ | △ | Status |",
        "|---|---|---|---|---|",
    ]
    for r in reports:
        app = apps.get(r.repo)
        rel = app and (app["release"] or app["preview"])
        version = f"`{rel['tag']}`" if rel else "none"
        status = "Conforms" if not r.count("required") else "Needs work"
        lines.append(f"| {app['name'] if app else r.repo} | {version} | {r.count('required')} | {r.count('recommended')} | {status} |")
    for r in reports:
        app = apps.get(r.repo)
        lines += ["", f"## {app['name'] if app else r.repo}", ""]
        if not r.items:
            lines.append("Nothing to fix.")
        order = {"required": 0, "recommended": 1, "note": 2}
        for level, text in sorted(r.items, key=lambda i: order[i[0]]):
            lines.append(f"- {marks[level]} {text}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT, help="directory for catalog.json and CONFORMANCE.md")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any app breaks a rule")
    args = ap.parse_args()

    registry = yaml.safe_load((ROOT / "registry.yml").read_text())
    owner = registry["owner"]
    gh = GitHub(token())

    try:
        import jsonschema
        app_validator = jsonschema.Draft202012Validator(json.loads((ROOT / "schema/app.schema.json").read_text()))
        catalog_schema = json.loads((ROOT / "schema/catalog.schema.json").read_text())
    except ImportError:
        print("jsonschema is not installed: skipping validation", file=sys.stderr)
        app_validator = catalog_schema = None

    repos = list(registry.get("repos", []))
    if registry.get("discover_topic"):
        found = gh.get(f"search/repositories?q=topic:{registry['discover_topic']}+user:{owner}&per_page=100") or {}
        known = {r.lower() for r in repos}
        repos += sorted(i["name"] for i in found.get("items", []) if i["name"].lower() not in known)

    apps, reports = {}, []
    for repo in repos:
        print(f"· {repo}", file=sys.stderr)
        app, rep = build_app(gh, owner, repo, app_validator)
        reports.append(rep)
        if app:
            apps[repo] = app

    ids = [a["id"] for a in apps.values()]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"duplicate app ids: {', '.join(sorted(dupes))}")

    when = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    catalog = {
        "schemaVersion": 1,
        "generatedAt": when,
        "publisher": registry["publisher"],
        "categories": registry["categories"],
        "apps": list(apps.values()),
    }
    if catalog_schema:
        jsonschema.validate(catalog, catalog_schema, format_checker=jsonschema.FormatChecker())

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    (args.out / "CONFORMANCE.md").write_text(conformance(reports, apps, when))
    broken = sum(1 for r in reports if r.count("required"))
    print(f"{len(apps)} apps in the catalog, {broken} with rule violations. See CONFORMANCE.md.", file=sys.stderr)
    return 1 if args.strict and broken else 0


if __name__ == "__main__":
    sys.exit(main())
