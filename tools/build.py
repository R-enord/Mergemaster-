# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the installable extension zip: dist/blend_master-<version>.zip

    python tools/build.py

Equivalent to Blender's own builder, which you can use instead:

    blender --command extension build --source-dir blend_master --output-dir dist

The archive holds the package files at its root (blender_manifest.toml next
to __init__.py), honouring ``[build] paths_exclude_pattern`` from the
manifest.  Needs only the Python standard library (3.11+ for tomllib).
"""

import fnmatch
import os
import sys
import tomllib
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "blend_master")
DIST = os.path.join(ROOT, "dist")


def excluded(rel_path, patterns):
    parts = rel_path.replace(os.sep, "/").split("/")
    for pattern in patterns:
        if pattern.endswith("/"):
            if any(fnmatch.fnmatch(part, pattern[:-1]) for part in parts[:-1]):
                return True
        elif any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def main():
    with open(os.path.join(SOURCE, "blender_manifest.toml"), "rb") as f:
        manifest = tomllib.load(f)
    patterns = manifest.get("build", {}).get("paths_exclude_pattern", [])
    out = os.path.join(DIST, "%s-%s.zip" % (manifest["id"], manifest["version"]))
    os.makedirs(DIST, exist_ok=True)
    files = []
    for folder, dirs, names in os.walk(SOURCE):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, SOURCE)
            if not excluded(rel, patterns):
                files.append((path, rel))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, rel in files:
            archive.write(path, rel.replace(os.sep, "/"))
    print("Built %s (%d files)" % (os.path.relpath(out, ROOT), len(files)))
    for _path, rel in files:
        print("  " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
