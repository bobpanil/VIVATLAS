"""A purpose chosen by hand beats the one worked out from tags.

The guess abstains rather than invent, which leaves cards on "undetermined" with no
way out; and it can be right in general yet wrong for the person keeping the card —
a design kit kept for its contrast rules is accessibility. So the choice sticks, and
it has to stick everywhere: the card, the filter, and the counts.
"""

from vivatlas import filters as flt
from vivatlas import purposes
from vivatlas.models import Artifact, Repository, Source


def _card(session, name: str = "some-kit", tags: tuple[str, ...] = ()) -> Artifact:
    from vivatlas.models import ArtifactTag, Tag

    src = session.query(Source).filter_by(kind="fake").first()
    if src is None:
        src = Source(kind="fake", base_url="https://x", display_name="Fake")
        session.add(src)
        session.flush()
    repo = Repository(
        source_id=src.id,
        external_id=f"ext-{name}",
        owner="acme",
        name=name,
        default_branch="main",
        html_url=f"https://git.example.com/acme/{name}",
    )
    session.add(repo)
    session.flush()
    art = Artifact(
        repository_id=repo.id, name=name, artifact_type="skill", shared=True, summary_short="x"
    )
    session.add(art)
    session.flush()
    for slug in tags:
        tag = session.query(Tag).filter_by(slug=slug).first()
        if tag is None:
            tag = Tag(slug=slug, label=slug, category="purpose")
            session.add(tag)
            session.flush()
        session.add(
            ArtifactTag(artifact_id=art.id, tag_id=tag.id, source="derived", confidence=0.9)
        )
    session.commit()
    return art


def test_by_key_only_accepts_real_purposes():
    assert purposes.by_key("accessibility").key == "accessibility"
    assert purposes.by_key("nonsense") is None
    assert purposes.by_key("") is None


def test_a_chosen_purpose_wins_over_the_tags(make_session):
    session = make_session()
    # Tags say security; the person says accessibility, and the person wins.
    art = _card(session, "scanner", ("security-scanning", "codeql"))
    assert purposes.detect_for(session, art.id, art.name)[0].key == "security"

    art.purpose_override = "accessibility"
    session.commit()
    assert purposes.detect_for(session, art.id, art.name)[0].key == "accessibility"

    # Cleared — back to reading the tags.
    art.purpose_override = ""
    session.commit()
    assert purposes.detect_for(session, art.id, art.name)[0].key == "security"


def test_a_card_the_tags_cannot_place_can_still_be_given_one(make_session):
    """The whole complaint: 'undetermined' with no way out."""
    session = make_session()
    art = _card(session, "mystery")
    assert purposes.detect_for(session, art.id, art.name)[0].key == "unknown"

    art.purpose_override = "design"
    session.commit()
    assert purposes.detect_for(session, art.id, art.name)[0].key == "design"


def test_resolve_reads_the_override_without_a_query(make_session):
    session = make_session()
    art = _card(session, "kit", ("web-accessibility", "wcag"))
    assert purposes.resolve(art, ["web-accessibility", "wcag"])[0].key == "accessibility"
    art.purpose_override = "design"
    assert purposes.resolve(art, ["web-accessibility", "wcag"])[0].key == "design"


def test_filtering_and_counts_follow_the_choice(make_session):
    """Or the filter would quietly disagree with the chip on the card."""
    session = make_session()
    art = _card(session, "scanner2", ("security-scanning", "codeql"))

    assert art.id in flt.purpose_matching_ids(session, "security")
    assert art.id not in flt.purpose_matching_ids(session, "accessibility")

    art.purpose_override = "accessibility"
    session.commit()

    assert art.id in flt.purpose_matching_ids(session, "accessibility")
    assert art.id not in flt.purpose_matching_ids(session, "security")
    counts = {o.value: o.count for o in flt.purpose_options(session)}
    assert counts.get("accessibility", 0) >= 1
