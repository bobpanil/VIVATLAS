"""Shared setup for the test database.

The schema is stood up the same way as production: create_all plus a virtual
search table. Otherwise the tests would exercise a schema that never really exists.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vivatlas.migrate import create_fts_table
from vivatlas.models import Base


@pytest.fixture
def make_session():
    def _make():
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            create_fts_table(conn)
        return sessionmaker(bind=engine)()

    return _make
