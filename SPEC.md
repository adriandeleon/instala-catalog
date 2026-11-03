# Instala release and catalog spec

Version 1 · draft · November 2026

This spec defines three things:

1. **The release format** every app ships on GitHub: which files, how they are named, and how they are checked.
2. **The app manifest** (`.instala.yml`), the one file a repository adds to join the catalog.
3. **The catalog** (`catalog.json`), which a builder generates from the first two. The home page, the install commands and the optional Instala app all read it.

"Instala" is a working name. Nothing here depends on it.

## Goals

- **Every app stands on its own.** A release is complete without Instala, the catalog or any package manager. This spec only standardizes what each app already publishes.
- **Adding an app costs one file.** A new repository adds `.instala.yml`, tags a release, and appears on the home page, in the install commands and in Instala. Nothing in the hub changes.
- **Build on what exists.** Termina's release pipeline (JReleaser, a signed and notarized macOS dmg, signed Linux packages, `checksums_sha256.txt`) is the reference. Most rules below describe what it already does.

---

## 1. Release format

### 1.1 Tags and versions

- Tag releases `v<version>`, where `<version>` is SemVer: `v0.26.0`, `v1.3.0`.
- A version with a pre-release suffix (`v0.15.0-rc1`) is published as a GitHub **pre-release**. The catalog lists it as a preview, never as the latest version.
- The version in the file names is the tag without the `v`.

### 1.2 File names

```
<Name>-<version>-<os>-<arch>.<ext>
```

| Part | Values |
|---|---|
| `Name` | The app's display name, as in the manifest: `Editora`, `Torrenta` |
| `version` | The tag without `v`: `0.26.0` |
| `os` | `macos`, `windows`, `linux` |
| `arch` | `x64`, `arm64` |
| `ext` | See 1.3 |

`arch` is always present. `Insula-0.1.0-linux.deb` does not conform: a client cannot tell which machine it is for.

### 1.3 Required and optional files

| Target | Required | Optional |
|---|---|---|
| `macos-arm64` | `.dmg`, signed with a Developer ID and notarized | |
| `macos-x64` | `.dmg`, signed and notarized | |
| `windows-x64` | `.msi`, per-user install (`jpackage --win-per-user-install`) | `.zip` (portable app image) |
| `linux-x64` | `.tar.gz` (app image, `bin/<Name>` starts it) | `.deb`, `.rpm`, `.AppImage`, `.asc` signatures |
| `linux-arm64` | Recommended: `.tar.gz` | `.deb`, `.rpm`, `.AppImage`, `.asc` signatures |

Why these choices:

- **The Linux `.tar.gz` is required** because it installs without root. Instala and the Linux install script unpack it into the user's home folder. `.deb` and `.rpm` remain the best choice for people who use apt or dnf, so keep shipping them.
- **The MSI should be per-user** so Instala can install and update it silently (`msiexec /qn`) without an admin prompt.
- **The dmg must be notarized** so it opens on first launch. Termina's workflow already does this. Copy its "Import signing certificate" and "Notarize the dmg" steps.
- Windows code signing is recommended but not required. Without it SmartScreen warns on first run. Say so in the release notes, as Termina does.

Other files (`.jar`, `RELEASE.md`, signing keys) may be attached. The catalog ignores anything it does not recognize.

### 1.4 Checksums and signatures

- Attach **`checksums_sha256.txt`** with one `<sha256>  <file name>` line for every file. This is JReleaser's default name, and Editora, Termina and Torrenta already use it. Rename `checksums.txt` in Modula, Nux and Insula.
- The catalog takes each file's SHA-256 from the GitHub API (`digest` on every release asset), so the checksum file is for people verifying by hand. Clients must still verify the hash after downloading.
- Detached `.asc` signatures next to a file (`Termina-0.14.0-linux-x64.deb.asc`) are optional. When present, the catalog links them.

### 1.5 Release notes

Start the release body with an **Install** section covering each OS, the way `Termina/.github/release-notes.tpl` does. Put the changelog after it. The catalog links to the release page for notes and does not copy them.

### 1.6 Telling the catalog about a release

The last job in each app's `release.yml` sends a `repository_dispatch` to the catalog repository so the catalog rebuilds right away (see `ci/notify-catalog.yml`). An app whose release workflow opens a draft (Modula) sends it from a separate workflow on `release: published` instead, since the catalog skips drafts. That event does not fire for a release published with the workflow's own token, which is why apps that publish directly use the job. If the token is missing or the call fails, nothing breaks: the catalog also rebuilds on a daily schedule.

---

## 2. App manifest: `.instala.yml`

The manifest goes in the root of the app's repository, at the commit the release is tagged from. The builder reads it at the release tag, so a release always matches the manifest it shipped with.

```yaml
id: termina                  # lowercase, stable forever; used in URLs, package names and install paths
name: Termina                # display name; also the <Name> in release file names
tagline: Terminal emulator that runs a real shell on a pseudo-terminal and draws to a canvas.
category: dev                # dev | net | radio | read | media | system | other
status: active               # preview | active | maintenance | archived
color: "#6563C4"             # accent colour, taken from the icon
keywords: [terminal, shell, console, pty]

icon: branding/termina-icon.svg          # default: branding/<id>-icon.svg; used by the web page
iconPng: src/main/resources/com/termina/icons/icon-256.png   # 256 px; used by Instala (JavaFX cannot draw SVG)
screenshots:                             # default: docs/screenshot.png, no alt text
  - path: docs/screenshot.png
    alt: Two tabs and a colorized directory listing in a shell session.

homepage: https://termina-project.dev    # default: the GitHub repository
requires: []                             # shown to users before install, e.g. "An RTL-SDR USB dongle."

packages:                                # only channels that are actually published
  homebrew: adriandeleon/tap/termina
  winget: AdrianDeLeon.Termina
```

Rules:

- `id`, `name`, `tagline`, `category` and `status` are required. Everything else has a default or may be left out.
- Paths are relative to the repository root. The builder checks that each one exists at the tag.
- New apps should keep icons at `branding/<id>-icon.svg` and `branding/<id>-icon-256.png`, as Modula does. The current apps use several layouts, which is why `iconPng` is a path and not a fixed name.
- `status` controls where the app appears:
  - `preview`: shown on the home page with a "Preview" label. It may have no release yet.
  - `active` and `maintenance`: shown normally.
  - `archived`: left out of the home page and the install commands. Instala keeps updating existing installs until the user removes the app.
- List a package channel only once it really publishes. The home page builds install commands only from what is listed here.

Until a repository has its own `.instala.yml`, the catalog repository can hold one for it in `apps/<repo>.instala.yml`. A file in the app's repository always wins.

---

## 3. Catalog: `catalog.json`

The builder (`tools/build_catalog.py`) writes `catalog.json` and `CONFORMANCE.md`. CI publishes the catalog as a static file, for example `https://apps.adeleon.dev/catalog.json`. It is the only thing clients fetch. They never call the GitHub API, so the 60-requests-per-hour limit for anonymous API calls never applies to users.

### 3.1 Shape

```jsonc
{
  "schemaVersion": 1,
  "generatedAt": "2026-11-02T15:04:05Z",
  "publisher": { "name": "Adrián De León", "url": "https://www.adeleon.dev" },
  "categories": [ { "id": "dev", "name": "Dev tools" } ],
  "apps": [
    {
      "id": "termina",
      "name": "Termina",
      "tagline": "…",
      "category": "dev",
      "status": "active",
      "color": "#6563C4",
      "keywords": ["terminal", "shell"],
      "icon": "https://raw.githubusercontent.com/adriandeleon/Termina/v0.14.0/branding/termina-icon.svg",
      "iconPng": "https://raw.githubusercontent.com/adriandeleon/Termina/v0.14.0/src/main/resources/com/termina/icons/icon-256.png",
      "screenshots": [ { "url": "…", "alt": "…" } ],
      "links": { "homepage": "…", "repository": "…", "releases": "…", "issues": "…" },
      "requires": [],
      "packages": { "homebrew": "adriandeleon/tap/termina", "winget": "AdrianDeLeon.Termina" },
      "release": {
        "version": "0.14.0",
        "tag": "v0.14.0",
        "publishedAt": "2026-11-01T01:33:49Z",
        "notesUrl": "https://github.com/adriandeleon/Termina/releases/tag/v0.14.0",
        "checksumsUrl": "…/checksums_sha256.txt",
        "assets": [
          {
            "os": "linux", "arch": "x64", "kind": "tar.gz",
            "name": "Termina-0.14.0-linux-x64.tar.gz",
            "url": "https://github.com/…/Termina-0.14.0-linux-x64.tar.gz",
            "size": 81234567,
            "sha256": "…",
            "signatureUrl": "…/Termina-0.14.0-linux-x64.tar.gz.asc"
          }
        ]
      },
      "preview": null
    }
  ]
}
```

- `release` is the latest full release, or `null` if there is none yet (Calcula today).
- `preview` is the latest pre-release, but only when it is newer than `release`. Otherwise it is `null`.
- `assets` contains only files that follow 1.2. Files that don't are listed in `CONFORMANCE.md` instead.
- Icon and screenshot URLs are pinned to the release tag, so they never change under a published version.
- The full contract is `schema/catalog.schema.json`. The builder validates against it before writing.

### 3.2 Compatibility

- `schemaVersion` changes only for breaking changes. Adding a field is not breaking: clients ignore fields they don't know.
- Clients must handle a `kind`, `os`, `arch`, `category` or `status` they don't recognize by skipping that entry, not by failing.

### 3.3 How clients pick a file

| Client | macOS | Windows | Linux |
|---|---|---|---|
| Instala | `dmg`: mount, copy the `.app` to `~/Applications` | `msi`: `msiexec /i … /qn` | `tar.gz`: unpack to `~/.local/share/instala/apps/<id>`, write a `.desktop` file |
| Install script | `brew` if listed, else `dmg` | `winget` if listed, else `msi` | `tar.gz` |
| Home page | `homebrew` command | `winget` command | install script |

When an app is not available for the user's OS and CPU, clients say so and skip it. They never fall back to a file for another CPU.

---

## 4. Adding a new app

1. Copy Termina's `release.yml` and `jreleaser.yml`, change the names, and keep the target list from 1.3.
2. Add `.instala.yml` (section 2) and `branding/<id>-icon.svg`.
3. Add the `notify-catalog` job (1.6).
4. Tag `v0.1.0`. The app appears in the catalog within minutes. Check `CONFORMANCE.md` for anything missing.

## 5. Where the current apps stand

`CONFORMANCE.md` is generated on every build and is the up-to-date list. The first run, on November 2, 2026, found:

| App | Latest | What to fix |
|---|---|---|
| Termina | v0.14.0 | Nothing. Reference implementation. |
| Editora | v0.26.0 | Nothing required. Add `docs/screenshot.png`. |
| Torrenta | v0.4.1 | Add the `macos-x64` dmg and the Linux `.tar.gz`. |
| Nux | v0.6.1 | Rename `checksums.txt`, add the Linux `.tar.gz`. |
| Modula | v1.3.0 | Rename `checksums.txt`, add the Linux `.tar.gz`. |
| Insula | v0.1.0 | Put the CPU type in every file name, split the dmg by CPU, rename `checksums.txt`, add the Linux `.tar.gz`. Until then the catalog lists no downloads for it. |
| Calcula | none | Publish a first release. |

Every app also needs its manifest moved from `apps/` in the catalog repository into its own repository as `.instala.yml`.

## 6. Files in this repository

| Path | What it is |
|---|---|
| `SPEC.md` | This document |
| `registry.yml` | Which repositories are in the catalog, the publisher and the category names |
| `apps/*.instala.yml` | Manifests staged for each app until they move into the app's repository |
| `schema/app.schema.json` | JSON Schema for `.instala.yml` |
| `schema/catalog.schema.json` | JSON Schema for `catalog.json` |
| `tools/build_catalog.py` | The builder: `python3 tools/build_catalog.py [--out DIR] [--strict]` |
| `README.md` | What this repository is, and where the published catalog lives |
| `.github/workflows/build-catalog.yml` | Rebuilds and publishes the catalog with GitHub Pages |
| `ci/notify-catalog.yml` | Job to add to each app's `release.yml` |
