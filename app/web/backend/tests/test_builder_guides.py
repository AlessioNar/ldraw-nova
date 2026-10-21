"""The foundation guides follow the optional TypeSafe key."""
import pytest

import environment_config
import toolkit


@pytest.fixture
def tree(tmp_path, monkeypatch):
    for path in (*toolkit.BUILDER_GUIDES, *toolkit.KEYWORD_GUIDE.values()):
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"content of {path}")
    monkeypatch.setattr(toolkit, "root", lambda: tmp_path)
    return tmp_path


def with_env(monkeypatch, **values):
    monkeypatch.setattr(environment_config, "snapshot", lambda: values)


@pytest.mark.parametrize("env", [{}, {"TYPESAFE_API_KEY": ""}, {"TYPESAFE_API_KEY": "   "}])
def test_without_a_key_the_keyword_guide_replaces_the_jev_guide(tree, monkeypatch, env):
    with_env(monkeypatch, **env)
    guides = toolkit.builder_guides()
    assert "--- docs/agent/reference-discovery-fts.md (complete) ---" in guides
    assert "reference-discovery.md" not in guides
    for path in toolkit.BUILDER_GUIDES:
        if path not in toolkit.KEYWORD_GUIDE:
            assert f"content of {path}" in guides


def test_with_a_key_the_full_guide_is_used_and_choice_is_stable(tree, monkeypatch):
    with_env(monkeypatch, TYPESAFE_API_KEY="private")
    guides = toolkit.builder_guides()
    assert "--- docs/agent/reference-discovery.md (complete) ---" in guides
    assert "reference-discovery-fts" not in guides and "private" not in guides
    assert toolkit.builder_guides() == guides


def test_shipped_keyword_guide_exists_and_is_smaller():
    source = toolkit.settings.TOOLKIT_DIR
    for full, short in toolkit.KEYWORD_GUIDE.items():
        if (source / short).is_file():
            assert len((source / short).read_text()) < len((source / full).read_text())
