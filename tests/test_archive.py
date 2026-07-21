import io
import tarfile

from vivatlas.archive import is_secret_file, read_archive


def make_tar(files: dict[str, bytes], top: str = "repo-abc123") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(name=f"{top}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_top_folder_is_stripped():
    contents = read_archive(make_tar({"SKILL.md": b"# hi"}))
    assert contents.paths == ["SKILL.md"]


def test_text_files_are_read():
    contents = read_archive(make_tar({"SKILL.md": b"# Brandkit"}))
    assert contents.get("SKILL.md").text == "# Brandkit"


def test_binary_files_are_listed_but_not_read():
    contents = read_archive(make_tar({"preview.svg": b"<svg/>", "logo.png": b"\x89PNG\x00"}))
    assert contents.get("logo.png").text is None
    assert contents.get("logo.png") is not None  # but present in the list


def test_git_internals_are_skipped():
    contents = read_archive(make_tar({"README.md": b"x", ".git/config": b"y"}))
    assert contents.paths == ["README.md"]


# --- secrets ---


def test_secret_files_are_never_read():
    contents = read_archive(
        make_tar(
            {
                ".env": b"GOOGLE_API_KEY=real-secret-value",
                "id_rsa": b"-----BEGIN PRIVATE KEY-----",
                "server.pem": b"-----BEGIN CERTIFICATE-----",
                "README.md": b"safe",
            }
        )
    )
    for path in (".env", "id_rsa", "server.pem"):
        assert contents.get(path).text is None, f"{path} was read but should not have been"
    assert contents.get("README.md").text == "safe"


def test_secret_content_never_reaches_doc_text():
    from vivatlas.detector import detect

    contents = read_archive(make_tar({"SKILL.md": b"# Tool", ".env": b"SECRET_TOKEN=abcdef123456"}))
    detection = detect(contents)
    assert "abcdef123456" not in detection.doc_text
    assert "SECRET_TOKEN" not in detection.doc_text


def test_example_files_are_not_secrets():
    # .env.example does not contain real values — safe to read.
    assert is_secret_file(".env.example") is False
    assert is_secret_file("config.sample") is False
    assert is_secret_file(".env") is True
    assert is_secret_file("app/.env") is True


def test_big_anchor_file_is_still_read():
    # Bug: the 200 KB ceiling silently dropped a 207 KB SKILL.md
    # (mvanhorn/last30days-skill), and the card came out with the text
    # "documentation missing". The anchor file must never be lost.
    big = b"# Skill\n" + b"x" * 300_000
    contents = read_archive(make_tar({"SKILL.md": big}))
    assert contents.get("SKILL.md").text is not None
    assert len(contents.get("SKILL.md").text) > 200_000


def test_absurdly_big_file_is_still_skipped():
    from vivatlas.archive import MAX_TEXT_BYTES

    contents = read_archive(make_tar({"huge.md": b"x" * (MAX_TEXT_BYTES + 1)}))
    assert contents.get("huge.md").text is None
