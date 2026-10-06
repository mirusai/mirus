import pytest


@pytest.fixture
def isolated_feature_registry(monkeypatch):
    """Keep definition registration local to each feature test."""
    import mirus.features.registry as registry
    from mirus.features.registry import Registry

    isolated = Registry()
    monkeypatch.setattr(registry, "_default_registry", isolated)
    return isolated
