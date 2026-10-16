"""The lock on a card: what the grid draws has to follow what the database says.

1.0.11 broke this without a single failing test: a second helper named like the
card's zone helper replaced it, so every card was drawn private whatever it was,
and a card made public looked private again after a refresh.
"""

import re

from sqlalchemy import select

from tests.test_live import _card, _owner_id
from tests.test_reviews import site  # noqa: F401
from vivatlas.models import Artifact, User


def _drawn(client, card_id):
    page = client.get("/").text
    zone = re.search(rf'data-id="{card_id}"[^>]*data-zone="(\w+)"', page)
    switch = re.search(rf'action="/artifact/{card_id}/visibility"', page)
    return (zone.group(1) if zone else None), bool(switch)


def test_making_my_card_public_shows_after_a_refresh(site):  # noqa: F811
    client, Local, (aid, _rid) = site
    with Local() as s:
        _card(s, aid, "mine", shared=False, owner_user_id=_owner_id(Local))
        s.commit()
        mine = s.scalar(select(Artifact.id).where(Artifact.name == "mine"))
    assert _drawn(client, mine) == ("private", True)

    r = client.post(f"/artifact/{mine}/visibility", headers={"Accept": "application/json"})
    assert r.json() == {"private": False, "zone": "common"}
    assert _drawn(client, mine) == ("common", True)

    client.post(f"/artifact/{mine}/visibility", headers={"Accept": "application/json"})
    assert _drawn(client, mine) == ("private", True)


def test_a_card_i_did_not_add_has_no_switch(site):  # noqa: F811
    client, Local, (aid, _rid) = site
    with Local() as s:
        other = User(email="p@x.com", display_name="Other", password_hash="h")
        s.add(other)
        s.flush()
        _card(s, aid, "theirs", shared=True, owner_user_id=other.id)
        s.commit()
        theirs = s.scalar(select(Artifact.id).where(Artifact.name == "theirs"))
    # Shared, and drawn so; the catalogue's own card (aid) has no owner either.
    assert _drawn(client, theirs) == ("common", False)
    assert _drawn(client, aid) == ("common", False)
