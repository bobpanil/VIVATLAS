"""The Add form: one screen, one Save.

A link (on its own, or with a note around it) goes into the same capture queue the
extension uses, and the person is back in the catalogue at once; the card turns up
there when it has been read. Only what has no link in it is looked up first.
VIVATLAS writes nothing to Git: a repository the finder suggests becomes a card the
same way a saved link does.
"""

import asyncio

import pytest
from sqlalchemy import select

from tests.test_reviews import site  # noqa: F401
from vivatlas import captures, web
from vivatlas.config import settings
from vivatlas.finder import FindResult
from vivatlas.models import Artifact, CaptureJob, User


def _no_model():
    raise RuntimeError("no AI in this test")


async def _meta(url, timeout=30.0):
    return {"title": "GitHub - octo/tool: Sketches to SVG", "description": "Sketches to SVG"}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(web, "build_text_model", _no_model)
    monkeypatch.setattr(web, "build_embedding_model", _no_model)
    monkeypatch.setattr(web, "fetch_page_meta", _meta)


@pytest.mark.parametrize(
    "text, url, rest",
    [
        ("https://github.com/octo/tool", "https://github.com/octo/tool", ""),
        ("github.com/octo/tool", "https://github.com/octo/tool", ""),
        ("www.example.com/x", "https://www.example.com/x", ""),
        ("see https://ex.com/a, for icons.", "https://ex.com/a", "see , for icons."),
        ("(see https://ex.com/a)", "https://ex.com/a", "(see )"),
        ("figma-to-svg", "", "figma-to-svg"),
        ("", "", ""),
    ],
)
def test_split_link(text, url, rest):
    assert web._split_link(text) == (url, rest)


def test_a_pasted_link_is_queued_and_the_catalogue_waits_for_it(site, offline):  # noqa: F811
    client, Local, _ids = site
    r = client.post(
        "/add",
        data={"source": "for the icons: github.com/octo/tool", "zone": "shared"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:300]
    assert r.headers["location"].startswith("/?saving=")
    assert "vivatlas_add_zone=shared" in r.headers.get("set-cookie", "")
    job_id = int(r.headers["location"].split("=", 1)[1])

    with Local() as s:
        job = s.get(CaptureJob, job_id)
        assert job.url == "https://github.com/octo/tool"
        assert job.text == "for the icons:" and job.text_kind == "note"
        assert job.shared is True and job.via == "web"

    assert client.get(f"/add/status?job={job_id}").json()["status"] == "pending"
    asyncio.run(captures.drain())
    d = client.get(f"/add/status?job={job_id}").json()
    assert d["status"] == "done" and d["artifact_id"]

    with Local() as s:
        art = s.get(Artifact, d["artifact_id"])
        assert art.artifact_type == "page" and art.shared is True


def test_the_zone_defaults_to_private_and_is_remembered(site, offline):  # noqa: F811
    client, Local, _ids = site
    r = client.get("/add")
    assert 'value="private" form="addform" checked' in r.text

    r = client.post("/add", data={"source": "https://ex.com/a", "zone": "nonsense"},
                    follow_redirects=False)
    job_id = int(r.headers["location"].split("=", 1)[1])
    with Local() as s:
        assert s.get(CaptureJob, job_id).shared is False

    assert 'value="private" form="addform" checked' in client.get("/add").text
    client.post("/add", data={"source": "https://ex.com/b", "zone": "shared"},
                follow_redirects=False)
    assert 'value="shared" form="addform" checked' in client.get("/add").text


def test_a_found_repository_is_saved_like_a_link(site, offline):  # noqa: F811
    client, Local, _ids = site
    r = client.post(
        "/add/link",
        data={"url": "https://github.com/octo/tool", "name": "octo/tool",
              "summary": "Sketches to SVG", "zone": "private"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/?saving=")
    asyncio.run(captures.drain())
    with Local() as s:
        art = s.scalar(select(Artifact).where(Artifact.name == "octo/tool"))
        assert art is not None and art.artifact_type == "page"
        assert art.shared is False and art.owner_user_id is not None


def test_a_name_is_looked_up_and_can_be_saved_as_a_note(site, offline, monkeypatch):  # noqa: F811
    client, Local, _ids = site
    seen = {}

    async def find(self, source, model):
        seen["source"] = source
        return FindResult(
            kind="text", source="iconkit", candidates=[], gist="draws icons", tool_name="iconkit"
        )

    monkeypatch.setattr(web.Finder, "find", find)
    r = client.post("/add", data={"source": "iconkit"})
    assert r.status_code == 200
    assert seen["source"] == "iconkit"
    assert 'action="/add/note"' in r.text and 'action="/add/save"' not in r.text

    r = client.post(
        "/add/note",
        data={"source": "iconkit", "name": "iconkit", "summary": "draws icons", "zone": "private"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/?added=1"
    with Local() as s:
        art = s.scalar(select(Artifact).where(Artifact.name == "iconkit"))
        assert art.artifact_type == "page" and art.shared is False


def test_a_typed_server_path_is_read_as_a_name(site, offline, monkeypatch, tmp_path):  # noqa: F811
    client, _Local, _ids = site
    seen = {}

    async def find(self, source, model):
        seen["source"] = source
        return FindResult(kind="text", source=source, candidates=[])

    monkeypatch.setattr(web.Finder, "find", find)
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"x")
    client.post("/add", data={"source": str(secret)})
    assert seen["source"] != str(secret)  # never handed to the finder as a file


def test_the_status_of_a_save_is_only_for_its_owner(site, offline):  # noqa: F811
    client, Local, _ids = site
    with Local() as s:
        other = User(email="z@x.com", display_name="Z", password_hash="h")
        s.add(other)
        s.flush()
        job = CaptureJob(url="https://ex.com/z", user_id=other.id, status="pending")
        s.add(job)
        s.commit()
        jid = job.id
    r = client.get(f"/add/status?job={jid}")
    assert r.status_code == 404 and r.json() == {"status": "gone"}
    # A mangled id ends the wait too, instead of a 422 the page keeps polling.
    for bad in ("nosuchjob", ""):
        r = client.get(f"/add/status?job={bad}")
        assert r.status_code == 404 and r.json() == {"status": "gone"}


def test_the_old_steps_are_gone(site):  # noqa: F811
    client, _Local, _ids = site
    for path in ("/add/plan", "/add/run", "/add/save", "/add/draft"):
        r = client.post(path, data={"url": "https://github.com/octo/tool"})
        assert r.status_code in (404, 405), (path, r.status_code)
