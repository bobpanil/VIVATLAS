"""GET /live: the stamp open pages poll to know when to fetch themselves again.

It has to move on anything the person would otherwise press refresh for (a new card,
a filled-in description, a favourite, a folder renamed on another device) and stay
put otherwise, or every open tab would refetch itself every few seconds. Someone
else's private cards must not move it: that would tell a person something changed in
a place they cannot see.
"""

from sqlalchemy import select

from tests.test_reviews import site  # noqa: F401
from vivatlas import live
from vivatlas.models import Artifact, Category, Favorite, Repository, User


def _card(s, src_from, name, **kw):
    src_id = s.get(Repository, s.get(Artifact, src_from).repository_id).source_id
    repo = Repository(source_id=src_id, external_id=name, owner="o", name=name,
                      default_branch="main", html_url=f"https://git.example.com/o/{name}")
    s.add(repo)
    s.flush()
    s.add(Artifact(repository_id=repo.id, name=name, artifact_type="skill",
                   confidence=0.9, **kw))


def _stamp(client):
    r = client.get("/live")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    return r.json()["v"]


def _owner_id(Local):
    with Local() as s:
        return s.scalar(select(User.id).where(User.email == "o@x.com"))


def test_the_stamp_is_steady_while_nothing_changes(site):  # noqa: F811
    client, _Local, _ids = site
    assert _stamp(client) == _stamp(client)


def test_a_new_card_moves_it(site):  # noqa: F811
    client, Local, (aid, _rid) = site
    before = _stamp(client)
    with Local() as s:
        _card(s, aid, "new", shared=True)
        s.commit()
    assert _stamp(client) != before


def test_a_filled_in_description_moves_it(site):  # noqa: F811
    client, Local, (aid, _rid) = site
    before = _stamp(client)
    with Local() as s:
        s.get(Artifact, aid).summary_short = "now with a description"
        s.commit()
    assert _stamp(client) != before


def test_a_favourite_moves_it(site):  # noqa: F811
    client, Local, (aid, _rid) = site
    before = _stamp(client)
    with Local() as s:
        s.add(Favorite(user_id=_owner_id(Local), artifact_id=aid))
        s.commit()
    assert _stamp(client) != before


def test_a_folder_renamed_elsewhere_moves_it(site):  # noqa: F811
    client, Local, _ids = site
    with Local() as s:
        s.add(Category(name="Icons", position=1))
        s.commit()
    before = _stamp(client)
    with Local() as s:
        s.scalar(select(Category)).name = "Icon sets"
        s.commit()
    assert _stamp(client) != before


def test_someone_elses_private_card_does_not_move_it(site):  # noqa: F811
    client, Local, (aid, _rid) = site
    with Local() as s:
        other = User(email="p@x.com", display_name="Other", password_hash="h")
        s.add(other)
        s.flush()
        s.add(Category(name="Mine only", owner_user_id=other.id))
        s.commit()
        other_id = other.id
    before = _stamp(client)
    with Local() as s:
        _card(s, aid, "secret", shared=False, owner_user_id=other_id)
        s.scalar(select(Category).where(Category.owner_user_id == other_id)).name = "x"
        s.commit()
    assert _stamp(client) == before


def test_signed_out_and_signed_in_stamps_differ_in_what_they_count(site):  # noqa: F811
    _client, Local, (aid, _rid) = site
    owner = _owner_id(Local)
    with Local() as s:
        s.add(Favorite(user_id=owner, artifact_id=aid))
        s.commit()
        anon = live.stamp(s, None)
        mine = live.stamp(s, owner)
    assert anon != mine


def test_signed_out_the_poll_goes_to_the_login(site):  # noqa: F811
    client, _Local, _ids = site
    client.cookies.clear()
    r = client.get("/live", follow_redirects=False)
    assert r.status_code in (303, 401)
