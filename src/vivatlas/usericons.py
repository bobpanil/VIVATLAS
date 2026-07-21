"""Default avatar set — classic busts with an orbit (VivAtlas brand).

The ready-made webp files live in static/usericons/avatar-NN.webp. A user gets a
random one at creation; in settings they can pick a different one or upload their
own photo (which takes precedence over the set — see /avatar in settings_web).

We derive the list of keys from the folder rather than hardcoding it: drop in a
file and it shows up in the picker, nothing else to change.
"""

import pathlib
import random

_DIR = pathlib.Path(__file__).parent / "static" / "usericons"

# Keys of the form "avatar-01" (no extension), in order. An empty list — if the
# folder wasn't shipped (e.g. in a stripped-down build); then rendering falls
# back to initials, and the settings picker is simply empty.
PRESETS: list[str] = sorted(p.stem for p in _DIR.glob("avatar-*.webp"))


def is_valid(key: str) -> bool:
    """Is the key from the set? Guards against an arbitrary value from the form."""
    return key in PRESETS


def random_preset() -> str:
    """A random key from the set (or '' if the set is empty)."""
    return random.choice(PRESETS) if PRESETS else ""


def path(key: str) -> pathlib.Path | None:
    """Path to the set's webp for the key, or None if the key isn't ours."""
    return _DIR / f"{key}.webp" if is_valid(key) else None


def read_bytes(key: str) -> bytes | None:
    """Bytes of the set's webp for the key, or None if the key/file is missing."""
    p = path(key)
    if p is not None and p.exists():
        return p.read_bytes()
    return None
