# Instala catalog

One list of Adrián De León's desktop apps, with their latest release for each OS and CPU type. The home page, the install commands and the optional Instala app all read it.

- Catalog: https://adriandeleon.github.io/instala-catalog/catalog.json
- Which releases are missing something: https://adriandeleon.github.io/instala-catalog/CONFORMANCE.md
- How it works, the release format apps follow, and how to add an app: [SPEC.md](SPEC.md)

The catalog is rebuilt when an app publishes a release, once a day, and on a push that changes the registry, a manifest, a schema or the builder (`.github/workflows/build-catalog.yml`).

To build it locally:

```sh
pip install pyyaml jsonschema
python3 tools/build_catalog.py --out site
```
