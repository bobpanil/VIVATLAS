"""Чтение архива репозитория.

Архив разбирается в памяти и выбрасывается. На диск ничего не распаковывается:
это и быстрее, и снимает вопрос о вредоносных путях внутри архива.
"""

import io
import tarfile
from dataclasses import dataclass

# Файлы с секретами не читаем никогда — вдруг кто-то закоммитил такое в
# открытый репозиторий. Проверяется по имени файла, а не по содержимому.
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

# Читаем только текст. Картинки и бинарники — по имени, без содержимого.
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

MAX_TEXT_BYTES = 200_000  # больше в описание всё равно не влезет


def is_secret_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    if name == ".env.example" or name.endswith(".example") or name.endswith(".sample"):
        return False  # образцы без настоящих значений — можно
    return any(p in name for p in SECRET_PATTERNS)


@dataclass
class RepoFile:
    path: str  # путь внутри репозитория, без верхней папки архива
    size: int
    text: str | None  # None для бинарных, больших и секретных


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
        """Первый найденный файл из перечисленных, в порядке приоритета."""
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
    """Архив Gitea завёрнут в одну папку — убираем её."""
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 else ""
