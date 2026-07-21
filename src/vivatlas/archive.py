"""Reading a repository archive.

The archive is parsed in memory and discarded. Nothing is unpacked to disk:
this is both faster and removes any concern about malicious paths inside the archive.
"""

import io
import tarfile
from dataclasses import dataclass

# Never read files with secrets — someone may have committed such a thing into
# a public repository. Checked by file name, not by content.
SECRET_PATTERNS = (
    ".env",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".git-credentials",
    "credentials",
    "secrets",
)

# Read text only. Images and binaries — by name, without content.
TEXT_SUFFIXES = (
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".sh",
    ".ps1",
    ".rb",
    ".go",
)

# The ceiling used to be 200 KB — "it won't fit in the description anyway". That was
# wrong: SKILL.md in mvanhorn/last30days-skill weighs 207 KB, didn't get read, and the
# card came out with the text "no documentation". Truncation for the description is done
# later and separately; here we must not cut — otherwise the reference file is lost entirely.
MAX_TEXT_BYTES = 2_000_000


def is_secret_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    if name == ".env.example" or name.endswith(".example") or name.endswith(".sample"):
        return False  # samples without real values — allowed
    return any(p in name for p in SECRET_PATTERNS)


@dataclass
class RepoFile:
    path: str  # path within the repository, without the archive's top folder
    size: int
    text: str | None  # None for binary, large, and secret files


@dataclass
class RepoContents:
    files: list[RepoFile]

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    def get(self, path: str) -> RepoFile | None:
        for f in self.files:
            if f.path.lower() == path.lower():
                return f
        return None

    def find(self, *names: str) -> RepoFile | None:
        """The first matching file from the listed names, in priority order."""
        for name in names:
            found = self.get(name)
            if found is not None:
                return found
        return None


def read_archive(blob: bytes) -> RepoContents:
    files: list[RepoFile] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            path = _strip_top_folder(member.name)
            if not path or path.startswith(".git/"):
                continue

            text: str | None = None
            if is_secret_file(path):
                text = None
            elif path.lower().endswith(TEXT_SUFFIXES) and member.size <= MAX_TEXT_BYTES:
                extracted = tar.extractfile(member)
                if extracted is not None:
                    text = extracted.read().decode("utf-8", errors="replace")

            files.append(RepoFile(path=path, size=member.size, text=text))
    return RepoContents(files=sorted(files, key=lambda f: f.path))


def _strip_top_folder(name: str) -> str:
    """A Gitea archive is wrapped in a single folder — strip it."""
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 else ""
