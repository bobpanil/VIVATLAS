from vivatlas import version


def test_build_label_from_ci_env(monkeypatch):
    monkeypatch.setenv("VIVATLAS_BUILD_VERSION", "1.0.3")
    monkeypatch.setenv("VIVATLAS_BUILD_SHA", "abc1234def567")
    monkeypatch.setenv("VIVATLAS_BUILD_DATE", "2026-07-24")
    version.build_info.cache_clear()
    try:
        info = version.build_info()
        assert info["version"] == "1.0.3"
        assert info["sha"] == "abc1234"  # shortened to 7 for display
        assert info["date"] == "2026-07-24"
        assert version.build_label() == "1.0.3 · abc1234 · 2026-07-24"
    finally:
        version.build_info.cache_clear()


def test_build_label_dev_fallback(monkeypatch):
    # No CI stamp: version shows the base with a -dev marker, and never raises.
    monkeypatch.delenv("VIVATLAS_BUILD_VERSION", raising=False)
    monkeypatch.delenv("VIVATLAS_BUILD_SHA", raising=False)
    monkeypatch.delenv("VIVATLAS_BUILD_DATE", raising=False)
    version.build_info.cache_clear()
    try:
        assert version.build_info()["version"].endswith("-dev")
        assert version.build_label().startswith(version.__version__)
    finally:
        version.build_info.cache_clear()
