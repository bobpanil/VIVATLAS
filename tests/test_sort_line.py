"""The order the catalogue is in: a button beside Filter, always there.

It used to be in the toolbar on a wide screen, inside Filters on a phone, and gone
during a search, so whether it could be seen depended on where you looked from. Now
it is the same control everywhere (the toolbar, and the top bar on a phone or
tablet), and a search can be put in order too: best match by default, or any
catalogue order for that search alone.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, text

from tests.test_reviews import site  # noqa: F401
from vivatlas import web
from vivatlas.migrate import promote_drafts
from vivatlas.models import Artifact, Repository


def _cards(Local, names):
    now = datetime.now(UTC)
    with Local() as s:
        tool = s.query(Artifact).one()
        ids = []
        for i, name in enumerate(names):
            repo = Repository(source_id=tool.repository.source_id, external_id=f"x{i}",
                              owner="o", name=name, default_branch="main",
                              html_url=f"https://git.example.com/o/{name}")
            s.add(repo)
            s.flush()
            a = Artifact(repository_id=repo.id, name=name, artifact_type="skill",
                         confidence=0.9, shared=True, created_at=now - timedelta(days=i + 1))
            s.add(a)
            s.flush()
            ids.append(a.id)
        s.commit()
    return ids


def _fake_search(Local, order):
    async def do_search(session, q, model, mode=None, limit=200):
        by_name = {a.name: a for a in session.query(Artifact)}
        return [SimpleNamespace(artifact=by_name[n], reasons=[]) for n in order]
    return do_search


def test_the_order_is_a_button_beside_filter(site):  # noqa: F811
    client, _Local, _ids = site
    r = client.get("/")
    toolbar = r.text[r.text.index('class="toolbar-right"'):r.text.index('id="filters-btn"')]
    assert 'id="sort-btn"' in toolbar
    assert 'id="sort-btn-top"' in r.text        # the phone and tablet top bar
    # No "Cards: N" line: the sidebar shows that number already.
    assert 'class="resultsline"' not in r.text
    assert 'class="sheet-sort"' not in r.text  # no second copy inside Filters
    assert "Best match" not in r.text          # only a search has a best match


def test_a_search_shows_the_order_too_best_match_first(site, monkeypatch):  # noqa: F811
    client, Local, _ids = site
    _cards(Local, ["bbb", "aaa"])
    monkeypatch.setattr(web, "build_embedding_model", lambda: None)
    monkeypatch.setattr(web, "do_search", _fake_search(Local, ["bbb", "tool", "aaa"]))

    r = client.get("/?q=thing")
    assert 'id="sort-btn"' in r.text
    assert "Best match" in r.text
    assert "Found: 3." in r.text                # a search's count stays: it's news
    body = r.text[r.text.index('class="grid'):]
    assert body.index(">bbb<") < body.index(">tool<") < body.index(">aaa<")


def test_a_search_can_be_put_in_name_order_without_changing_the_catalogue(site, monkeypatch):  # noqa: F811
    client, Local, _ids = site
    _cards(Local, ["bbb", "aaa"])
    monkeypatch.setattr(web, "build_embedding_model", lambda: None)
    monkeypatch.setattr(web, "do_search", _fake_search(Local, ["bbb", "tool", "aaa"]))

    r = client.get("/?q=thing&sort=name")
    body = r.text[r.text.index('class="grid'):]
    assert body.index(">aaa<") < body.index(">bbb<") < body.index(">tool<")
    assert "vivatlas_sort" not in r.headers.get("set-cookie", "")  # that search only


def test_a_new_search_starts_in_best_match_order(site):  # noqa: F811
    client, _Local, _ids = site
    client.get("/?sort=name")  # the catalogue is kept in name order
    r = client.get("/")
    form = r.text[r.text.index('<form'):]
    form = form[: form.index("</form>")]
    assert 'name="sort"' not in form  # the search box doesn't carry it into a search


def test_relevance_is_never_the_catalogue_order(site):  # noqa: F811
    client, _Local, _ids = site
    r = client.get("/?sort=relevance")
    assert r.status_code == 200
    assert "vivatlas_sort" not in r.headers.get("set-cookie", "")
    assert "Best match" not in r.text


def test_leftover_drafts_join_the_catalogue(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE artifacts (id INTEGER PRIMARY KEY, artifact_type TEXT)"))
        conn.execute(
            text("INSERT INTO artifacts (artifact_type) VALUES ('draft'), ('skill'), ('draft')")
        )
        assert promote_drafts(conn) == 2
        assert promote_drafts(conn) == 0
        kinds = sorted(r[0] for r in conn.execute(text("SELECT artifact_type FROM artifacts")))
    assert kinds == ["page", "page", "skill"]
