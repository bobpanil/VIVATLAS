#!/usr/bin/env bash
# The VIVATLAS Clipper on addons.mozilla.org (AMO).
#
#   scripts/firefox-addon.sh build   lint and pack dist/firefox/vivatlas-clipper-<version>.zip
#   scripts/firefox-addon.sh sign    the same, then submit it to AMO as a listed version
#
# The package holds exactly the files the server's Settings download ships (_EXT_FILES
# in settings_web.py), with config.js left blank, so the store build asks for the server
# address once. Plain source, no build step: AMO needs no separate source upload.
#
# sign needs the AMO API key pair (addons.mozilla.org, Tools > Manage API Keys) in
# WEB_EXT_API_KEY / WEB_EXT_API_SECRET, and the reviewers' demo server and test account
# in AMO_REVIEWER_SERVER / AMO_REVIEWER_EMAIL / AMO_REVIEWER_PASSWORD. Those are filled
# into extension/amo/reviewer-notes.txt only in a temporary copy, never in the repository.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
ext="$root/extension"
amo="$ext/amo"
out="$root/dist/firefox"
stage="$out/stage"
webext=(npx --yes web-ext@10.7.0)

files="$(python3 - "$root/src/vivatlas/settings_web.py" <<'EOF'
import ast, sys
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_EXT_FILES" for t in node.targets):
        print("\n".join(ast.literal_eval(node.value)))
EOF
)"
[ -n "$files" ] || { echo "Could not read _EXT_FILES from settings_web.py." >&2; exit 1; }

rm -rf "$stage"
while IFS= read -r f; do
  mkdir -p "$stage/$(dirname "$f")"
  cp "$ext/$f" "$stage/$f"
done <<< "$files"
version="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$stage/manifest.json")"

"${webext[@]}" lint --source-dir "$stage"
"${webext[@]}" build --source-dir "$stage" --artifacts-dir "$out" \
  --filename "vivatlas-clipper-$version.zip" --overwrite-dest

case "${1:-build}" in
  build) ;;
  sign)
    : "${WEB_EXT_API_KEY:?Set WEB_EXT_API_KEY (AMO JWT issuer).}"
    : "${WEB_EXT_API_SECRET:?Set WEB_EXT_API_SECRET (AMO JWT secret).}"
    : "${AMO_REVIEWER_SERVER:?Set AMO_REVIEWER_SERVER (demo server for Mozilla reviewers).}"
    : "${AMO_REVIEWER_EMAIL:?Set AMO_REVIEWER_EMAIL (its test account).}"
    : "${AMO_REVIEWER_PASSWORD:?Set AMO_REVIEWER_PASSWORD.}"
    meta="$(mktemp)"
    trap 'rm -f "$meta"' EXIT
    python3 - "$amo/metadata.json" "$amo/reviewer-notes.txt" "$meta" <<'EOF'
import json, os, sys
meta = json.load(open(sys.argv[1], encoding="utf-8"))
notes = open(sys.argv[2], encoding="utf-8").read()
notes = notes.replace("{server}", os.environ["AMO_REVIEWER_SERVER"])
notes = notes.replace("{email}", os.environ["AMO_REVIEWER_EMAIL"])
notes = notes.replace("{password}", os.environ["AMO_REVIEWER_PASSWORD"])
meta.setdefault("version", {})["approval_notes"] = notes
json.dump(meta, open(sys.argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
EOF
    "${webext[@]}" sign --channel listed --source-dir "$stage" --artifacts-dir "$out" \
      --amo-metadata "$meta" --approval-timeout 0
    ;;
  *) echo "usage: $0 [build|sign]" >&2; exit 2 ;;
esac
