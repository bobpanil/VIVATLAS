from vivatlas.config import Settings


def make(**kw) -> Settings:
    base = {
        "gitea_url": "https://git.example.com",
        "gitea_token": "test-gitea-token-not-real",
        "github_token": "ghp_secrettoken",
        "google_api_key": "test-google-key-not-real",
        "secret_key": "secret-key-of-the-door",
    }
    base.update(kw)
    return Settings(**base)


def test_secrets_are_not_in_the_text_of_an_error():
    # The real case that prompted this rule: a test failed on the
    # settings line, and pydantic dumped the Gitea token and the Google
    # key in full, in plain text, into the error message.
    text = repr(make())
    for secret in (
        "test-gitea-token-not-real",
        "ghp_secrettoken",
        "test-google-key-not-real",
        "secret-key-of-the-door",
    ):
        assert secret not in text, secret
    assert "***hidden***" in text


def test_str_hides_them_too():
    assert "test-gitea-token-not-real" not in str(make())


def test_non_secrets_are_still_visible():
    # Hiding everything indiscriminately would make debugging impossible.
    text = repr(make())
    assert "git.example.com" in text
    assert "gemini" in text


def test_empty_secret_is_shown_as_empty_not_as_hidden():
    # "***hidden***" instead of emptiness would lie: the user would think the token
    # is set, and would hunt for the bug in the wrong place.
    text = repr(make(gitea_token=""))
    assert "gitea_token=''" in text


def test_the_value_itself_is_still_usable():
    # We only hide the display. The program still needs the full token.
    assert make().gitea_token == "test-gitea-token-not-real"


def test_gitea_url_is_empty_by_default():
    # A specific server's address used to be here, and any fresh install would go
    # scanning someone else's Gitea, just because the user never looked in .env.
    assert Settings(_env_file=None).gitea_url == ""
