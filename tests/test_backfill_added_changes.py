"""init-db gives every card that never recorded "added" one, exactly once."""

from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from vivatlas.migrate import backfill_added_changes
from vivatlas.models import Artifact, Base, Change, Repository, Source


def test_backfill_adds_one_added_event_per_card_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}", future=True)
    Base.metadata.create_all(engine)
    made = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    with Session(engine) as s:
        src = Source(kind="draft", base_url="", display_name="Drafts")
        s.add(src)
        s.flush()
        ids = []
        for i in range(3):
            r = Repository(source_id=src.id, external_id=f"d{i}", owner="x", name=f"n{i}",
                           default_branch="", html_url="")
            s.add(r)
            s.flush()
            a = Artifact(repository_id=r.id, name=f"reel {i}", artifact_type="page",
                         created_at=made)
            s.add(a)
            s.flush()
            ids.append((r.id, a.id))
        # One card already has its "added" event: it must not get a second.
        s.add(Change(kind="added", repository_id=ids[0][0], artifact_id=ids[0][1], title="x"))
        s.commit()

    with engine.begin() as conn:
        assert backfill_added_changes(conn) == 2
    with engine.begin() as conn:
        assert backfill_added_changes(conn) == 0

    with Session(engine) as s:
        for _rid, aid in ids:
            n = s.scalar(select(func.count()).select_from(Change).where(
                Change.artifact_id == aid, Change.kind == "added"))
            assert n == 1
        backfilled = s.scalars(select(Change).where(Change.artifact_id == ids[1][1])).one()
        assert backfilled.created_at.replace(tzinfo=None) == made.replace(tzinfo=None)
        assert backfilled.title == "reel 1"
