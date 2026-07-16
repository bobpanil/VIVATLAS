from skill_atlas.remap import _leaf, target_for


def test_leaf_of_a_folder():
    assert _leaf("design-md/airbnb") == "airbnb"


def test_leaf_skips_the_anchor_file():
    # Путь ведёт к файлу — папка инструмента над ним, а не сам файл.
    assert _leaf("design-md/airbnb/DESIGN.md") == "airbnb"
    assert _leaf("skills/last30days/SKILL.md") == "last30days"


def test_leaf_of_empty_is_empty():
    assert _leaf("") == ""


def test_lone_source_becomes_owner_repo():
    # Один инструмент = один репозиторий: имя как на GitHub, без хвоста.
    assert target_for("mvanhorn/last30days-skill", "skills/last30days/SKILL.md", False) == (
        "mvanhorn",
        "last30days-skill",
    )


def test_owner_case_preserved():
    assert target_for("Onflow-AI/Avenir-UX", "", False) == ("Onflow-AI", "Avenir-UX")


def test_shared_source_carries_the_folder():
    # 74 набора из одного awesome-design-md должны разойтись.
    a = target_for("VoltAgent/awesome-design-md", "design-md/airbnb/DESIGN.md", True)
    b = target_for("VoltAgent/awesome-design-md", "design-md/apple/DESIGN.md", True)
    assert a == ("VoltAgent", "awesome-design-md-airbnb")
    assert b == ("VoltAgent", "awesome-design-md-apple")
    assert a != b
