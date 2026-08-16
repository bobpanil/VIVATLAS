"""Turning an order around.

Each order has a natural direction — a name reads A→Z, "recently added" reads
newest-first — and the toggle says the other one outright. The tie-breaker must not
turn with it: reversing "recently added" should hand back the oldest first, not
shuffle the cards that happen to share a date.
"""

from datetime import UTC, datetime, timedelta

from vivatlas import filters as flt
from vivatlas.models import Artifact, Repository, Source


def _order_sql(sort: str, direction: str = "") -> str:
    return " , ".join(str(c) for c in flt.sort_order(sort, direction))


def test_each_order_has_a_natural_direction():
    assert flt.descending_by_default("added")
    assert flt.descending_by_default("updated")
    assert not flt.descending_by_default("name")
    assert not flt.descending_by_default("")


def test_left_alone_the_orders_run_the_way_they_read():
    assert "DESC" in _order_sql("added")           # newest first
    assert "DESC" in _order_sql("updated")
    assert "DESC" not in _order_sql("name")        # A→Z
    assert "DESC" not in _order_sql("")


def test_asking_turns_any_of_them_around():
    assert "DESC" not in _order_sql("added", "asc")     # oldest first
    assert "DESC" not in _order_sql("updated", "asc")
    assert "DESC" in _order_sql("name", "desc")         # Z→A

    # Saying the direction it already runs changes nothing.
    assert _order_sql("added", "desc") == _order_sql("added")
    assert _order_sql("name", "asc") == _order_sql("name")


def test_nonsense_direction_falls_back_to_natural():
    assert _order_sql("added", "sideways") == _order_sql("added")
    assert _order_sql("name", "DESC") == _order_sql("name")   # only lowercase is meant


def test_the_tie_breaker_does_not_turn_with_it():
    """Cards sharing a date stay A→Z whichever way the dates run."""
    for direction in ("", "asc", "desc"):
        clauses = flt.sort_order("added", direction)
        assert len(clauses) == 2
        assert "DESC" not in str(clauses[1])   # the name tie-breaker


def test_the_direction_rides_along_in_links():
    f = flt.Filters(sort="added", dir="asc", type="skill")
    assert f.as_query()["dir"] == "asc"
    # Changing the order keeps the rest of the query intact.
    assert f.as_query(sort="name")["type"] == "skill"
    # Empty direction is left out rather than written as dir=
    assert "dir" not in flt.Filters(sort="added").as_query()


def test_sorting_is_still_not_counted_as_a_filter():
    """It hides nothing, so it mustn't light up the filter badge."""
    assert not flt.Filters(sort="added", dir="asc").active()


def test_turning_it_around_really_reverses_the_rows(make_session):
    """The clauses are one thing; what comes back is another."""
    session = make_session()
    src = Source(kind="fake", base_url="https://x", display_name="Fake")
    session.add(src)
    session.flush()
    now = datetime.now(UTC)
    for i, name in enumerate(["ccc", "aaa", "bbb"]):
        repo = Repository(
            source_id=src.id, external_id=f"e{i}", owner="acme", name=name,
            default_branch="main", html_url=f"https://git.example.com/acme/{name}",
        )
        session.add(repo)
        session.flush()
        session.add(
            Artifact(
                repository_id=repo.id, name=name, artifact_type="skill", shared=True,
                summary_short="x", created_at=now - timedelta(days=i),
            )
        )
    session.commit()

    from sqlalchemy import select

    def names(sort, direction=""):
        return [
            a.name
            for a in session.scalars(select(Artifact).order_by(*flt.sort_order(sort, direction)))
        ]

    assert names("name") == ["aaa", "bbb", "ccc"]
    assert names("name", "desc") == ["ccc", "bbb", "aaa"]
    # created_at descends by index, so newest-first is the order they were made in.
    assert names("added") == ["ccc", "aaa", "bbb"]
    assert names("added", "asc") == ["bbb", "aaa", "ccc"]
