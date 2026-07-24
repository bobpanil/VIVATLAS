"""Build identity, surfaced in Admin so a running deployment can be matched to a
specific build at a glance ("am I on the latest image or not?").

The build number, commit and date are stamped into the image at build time by CI
(``.github/workflows/docker.yml`` -> ``Dockerfile`` ARG/ENV). Outside the image
(local dev) we fall back to ``git``; failing even that, a plain ``dev`` marker.
"""

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from vivatlas import __version__

# src/vivatlas/version.py -> repo root is three levels up. Used only for the
# local-dev git fallback; inside the container there is no .git and env wins.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=2,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


@lru_cache(maxsize=1)
def build_info() -> dict:
    """``{'version', 'sha', 'date'}`` — CI-stamped env first, git fallback.

    ``version`` is a semver that ticks up one patch per published build
    (``1.0.0`` -> ``1.0.1`` -> ``1.0.2`` …), stamped by CI. Outside the image it
    shows the base version with a ``-dev`` marker. ``sha``/``date`` pin the exact
    commit. Never raises — on a machine with neither env nor git it still returns.
    """
    ver = os.environ.get("VIVATLAS_BUILD_VERSION", "").strip()
    sha = os.environ.get("VIVATLAS_BUILD_SHA", "").strip()
    date = os.environ.get("VIVATLAS_BUILD_DATE", "").strip()
    if not ver:
        ver = f"{__version__}-dev"
    if not sha:
        sha = _git("rev-parse", "--short=7", "HEAD")
    if not date:
        date = _git("show", "-s", "--format=%cd", "--date=short", "HEAD")
    return {
        "version": ver,
        "sha": (sha or "")[:7],
        "date": (date or "")[:10],
    }


def build_label() -> str:
    """One compact line for the UI, e.g. ``1.0.3 · a1b2c3d · 2026-07-24``
    (or ``1.0.0-dev · a1b2c3d`` locally)."""
    info = build_info()
    parts = [info["version"]]
    if info["sha"]:
        parts.append(info["sha"])
    if info["date"]:
        parts.append(info["date"])
    return " · ".join(parts)
