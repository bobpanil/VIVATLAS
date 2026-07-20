#!/bin/sh
# VivAtlas container entrypoint.
# Keeps all mutable state in the mounted data dir, then starts the web server.
set -e

DATA_DIR="${VIVATLAS_DATA:-/data}"
mkdir -p "$DATA_DIR"
cd "$DATA_DIR"          # so serve's logs/ land in the volume, not the image layer

# Stable SECRET_KEY. Prefer the value passed in the environment; otherwise
# persist a generated one in the volume. It MUST stay constant across restarts —
# changing it logs everyone out and makes stored (encrypted) tokens unreadable.
if [ -z "${SECRET_KEY:-}" ]; then
  if [ -f "$DATA_DIR/secret_key" ]; then
    SECRET_KEY="$(cat "$DATA_DIR/secret_key")"
  else
    SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
    printf '%s' "$SECRET_KEY" > "$DATA_DIR/secret_key"
    echo "vivatlas: generated a SECRET_KEY and stored it at $DATA_DIR/secret_key"
  fi
  export SECRET_KEY
fi

# Create or upgrade tables on every start. Idempotent, and after a code upgrade
# it adds any new columns/indexes (serve itself does no migration).
python -m vivatlas.cli init-db

# Optional demo data: ~200 cards built from real GitHub repos. Idempotent — it
# skips repos already present. Turn on with VIVATLAS_SEED=1 (first run only).
if [ "${VIVATLAS_SEED:-0}" = "1" ]; then
  echo "vivatlas: seeding demo cards..."
  python /app/scripts/seed_mock.py || echo "vivatlas: seed skipped/failed"
fi

exec python -m vivatlas.cli serve --host 0.0.0.0 --port 8710
