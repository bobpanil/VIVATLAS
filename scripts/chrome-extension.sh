#!/usr/bin/env bash
# The VIVATLAS Clipper on the Chrome Web Store.
#
#   scripts/chrome-extension.sh   pack dist/chrome/vivatlas-clipper-<version>.zip
#
# The package holds exactly the files the server's Settings download ships (_EXT_FILES
# in settings_web.py), with config.js left blank, so the store build asks for the server
# address once. The manifest loses its Firefox-only block (browser_specific_settings),
# as the Settings download's Chrome build does. Upload the zip in the Chrome Web Store
# developer dashboard; the listing texts are in extension/cws/.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/dist/chrome"
mkdir -p "$out"

python3 - "$root" "$out" <<'EOF'
import ast, json, sys, zipfile
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
ext = root / "extension"
tree = ast.parse((root / "src/vivatlas/settings_web.py").read_text(encoding="utf-8"))
files = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
             and any(getattr(t, "id", "") == "_EXT_FILES" for t in n.targets))

manifest = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
manifest.pop("browser_specific_settings", None)
dest = out / f"vivatlas-clipper-{manifest['version']}.zip"

with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
    for rel in files:
        if rel == "manifest.json":
            z.writestr(rel, json.dumps(manifest, indent=2) + "\n")
        else:
            z.write(ext / rel, arcname=rel)
print(dest.relative_to(root), f"({dest.stat().st_size // 1024} KB, {len(files)} files)")
EOF
