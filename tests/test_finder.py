from skill_atlas.finder import (
    classify,
    extract_bare_repos,
    extract_repos,
    parse_og,
)

# --- что нам дали ---


def test_github_link_is_recognised():
    assert classify("https://github.com/DeusData/codebase-memory-mcp") == "github"
    assert classify("github.com/VoltAgent/awesome-design-md") == "github"


def test_other_site_is_web():
    assert classify("https://www.facebook.com/share/r/1Ek3xcL84A/") == "web"
    assert classify("https://voltagent.dev/") == "web"


def test_words_are_text():
    assert classify("скил который собирает новости за 30 дней") == "text"


def test_file_kind_comes_from_extension(tmp_path):
    png = tmp_path / "shot.png"
    png.write_bytes(b"x")
    mp4 = tmp_path / "reel.mp4"
    mp4.write_bytes(b"x")
    assert classify(str(png)) == "image"
    assert classify(str(mp4)) == "video"


def test_missing_file_is_not_an_image():
    # Путь к несуществующему файлу — просто слова, а не картинка.
    assert classify("C:/нет/такого/файла.png") == "text"


# --- ссылки целиком ---


def test_link_from_a_reel_caption():
    # Настоящий случай: подпись под рилсом на скриншоте.
    heard = (
        "Ссылка на репозиторий 👉 Больше связок в моём тг по ссылке в профиле. "
        "Ссылка: https://github.com/DeusData/codebase-memory-mcp У меня всё про..."
    )
    assert extract_repos(heard) == ["DeusData/codebase-memory-mcp"]


def test_bare_rule_alone_would_lose_that_link():
    # Почему полные ссылки разбираются отдельно и первыми: правило для голого
    # "владелец/репозиторий" спотыкается о github.com/ и теряет адрес целиком.
    heard = "Ссылка: https://github.com/DeusData/codebase-memory-mcp"
    assert "DeusData/codebase-memory-mcp" not in extract_bare_repos(heard)


def test_link_variants():
    assert extract_repos("см. https://github.com/a/b.git") == ["a/b"]
    assert extract_repos("(https://github.com/a/b)") == ["a/b"]
    assert extract_repos("https://github.com/a/b/tree/main/skills/x") == ["a/b"]


def test_no_repeats():
    text = "github.com/a/b и ещё раз github.com/a/b"
    assert extract_repos(text) == ["a/b"]


def test_common_github_links_are_not_tools():
    text = "https://github.com/features/actions и https://github.com/topics/mcp"
    assert extract_repos(text) == []


# --- голый адрес: так пишут на картинках ---


def test_bare_address_from_a_screenshot():
    # Карточка GitHub на скриншоте: имя написано, но ссылки нет.
    assert extract_bare_repos("VoltAgent/awesome-design-md 102k stars") == [
        "VoltAgent/awesome-design-md"
    ]


def test_ordinary_phrases_are_not_addresses():
    assert extract_bare_repos("вход/выход") == []
    assert extract_bare_repos("true/false и yes/no") == []
    assert extract_bare_repos("скорость 60 km/ms") == []


def test_domain_is_not_an_owner():
    assert extract_bare_repos("зайдите на voltagent.dev/docs") == []


def test_bare_finds_are_limited():
    text = " ".join(f"own{i}/repo{i}" for i in range(10))
    assert len(extract_bare_repos(text)) == 3


# --- страница ---


def test_og_tags_both_ways_round():
    html = """
    <meta property="og:title" content="Кино про скиллы">
    <meta content="https://x/v.mp4" property="og:video">
    """
    og = parse_og(html)
    assert og["title"] == "Кино про скиллы"
    assert og["video"] == "https://x/v.mp4"


def test_no_og_tags_is_empty_not_an_error():
    assert parse_og("<html><body>ничего</body></html>") == {}


def test_same_repo_written_differently_is_one_repo():
    # Настоящий случай: на voltagent.dev ссылки написаны и так, и так.
    # Для GitHub регистр во владельце не важен — репозиторий один.
    text = "github.com/VoltAgent/voltagent и github.com/voltagent/voltagent"
    assert extract_repos(text) == ["VoltAgent/voltagent"]
