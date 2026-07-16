from vivatlas.config import Settings


def make(**kw) -> Settings:
    base = {
        "gitea_url": "https://git.example.com",
        "gitea_token": "d29ad206dd36217610f3d95d6c458a75",
        "github_token": "ghp_секретныйтокен",
        "google_api_key": "AQ.Ab8RN6KZ_wcHuH5eiBUy6",
        "secret_key": "главный-ключ-двери",
    }
    base.update(kw)
    return Settings(**base)


def test_secrets_are_not_in_the_text_of_an_error():
    # Настоящий случай, из-за которого это правило и появилось: тест упал на
    # строчке с настройками, и pydantic вывалил в сообщение об ошибке токен
    # Gitea и ключ Google целиком, в открытом виде.
    text = repr(make())
    for secret in (
        "d29ad206dd36217610f3d95d6c458a75",
        "ghp_секретныйтокен",
        "AQ.Ab8RN6KZ_wcHuH5eiBUy6",
        "главный-ключ-двери",
    ):
        assert secret not in text, secret
    assert "***скрыто***" in text


def test_str_hides_them_too():
    assert "d29ad206dd36217610f3d95d6c458a75" not in str(make())


def test_non_secrets_are_still_visible():
    # Скрывать всё подряд — значит сделать отладку невозможной.
    text = repr(make())
    assert "git.example.com" in text
    assert "gemini" in text


def test_empty_secret_is_shown_as_empty_not_as_hidden():
    # "***скрыто***" вместо пустоты соврало бы: человек решил бы, что токен
    # задан, и искал бы ошибку не там.
    text = repr(make(gitea_token=""))
    assert "gitea_token=''" in text


def test_the_value_itself_is_still_usable():
    # Скрываем только показ. Программе токен по-прежнему нужен целиком.
    assert make().gitea_token == "d29ad206dd36217610f3d95d6c458a75"


def test_gitea_url_is_empty_by_default():
    # Тут стоял адрес конкретного сервера, и любая свежая установка пошла бы
    # сканировать чужую Gitea, просто потому что человек не заглянул в .env.
    assert Settings(_env_file=None).gitea_url == ""
