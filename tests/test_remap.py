from vivatlas.remap import _leaf, target_for


def test_leaf_of_a_folder():
    assert _leaf("design-md/airbnb") == "airbnb"


def test_leaf_skips_the_anchor_file():
    # The path points to a file — the tool's folder is above it, not the file itself.
    assert _leaf("design-md/airbnb/DESIGN.md") == "airbnb"
    assert _leaf("skills/last30days/SKILL.md") == "last30days"


def test_leaf_of_empty_is_empty():
    assert _leaf("") == ""


def test_lone_source_becomes_owner_repo():
    # One tool = one repository: the name as on GitHub, without a suffix.
    assert target_for("mvanhorn/last30days-skill", "skills/last30days/SKILL.md", False) == (
        "mvanhorn",
        "last30days-skill",
    )


def test_owner_case_preserved():
    assert target_for("Onflow-AI/Avenir-UX", "", False) == ("Onflow-AI", "Avenir-UX")


def test_shared_source_carries_the_folder():
    # 74 sets from a single awesome-design-md should split apart.
    a = target_for("VoltAgent/awesome-design-md", "design-md/airbnb/DESIGN.md", True)
    b = target_for("VoltAgent/awesome-design-md", "design-md/apple/DESIGN.md", True)
    assert a == ("VoltAgent", "awesome-design-md-airbnb")
    assert b == ("VoltAgent", "awesome-design-md-apple")
    assert a != b
