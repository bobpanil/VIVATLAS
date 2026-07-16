"""Общая подготовка тестовой базы.

Схема поднимается так же, как боевая: create_all плюс виртуальная таблица
поиска. Иначе тесты гоняли бы схему, которой в жизни не существует.
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
