import pytest

from vivatlas.providers.github import _account_from


@pytest.mark.parametrize(
    "given, expected",
    [
        ("bobpanil", "bobpanil"),
        ("https://github.com/bobpanil", "bobpanil"),
        ("https://github.com/bobpanil/", "bobpanil"),
        ("github.com/bobpanil", "bobpanil"),
        ("  https://github.com/bobpanil  ", "bobpanil"),
        # The regression: a full profile URL pasted into the "account" field, which
        # earlier got prefixed a second time and parsed to "https:" (→ 404).
        ("https://github.com/https://github.com/bobpanil", "bobpanil"),
        ("https://github.com/orgname/", "orgname"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_account_from_extracts_bare_account(given, expected):
    assert _account_from(given) == expected
