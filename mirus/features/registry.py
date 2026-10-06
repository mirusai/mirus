"""Lazy process-local storage for uncompiled feature declarations."""

from .definitions import (
    DeclarationSnapshot,
    FeatureDeclaration,
    FieldDeclaration,
)


class Registry:
    """Collect declarations without validation or runtime compilation."""

    def __init__(self) -> None:
        self._fields: list[FieldDeclaration] = []
        self._features: list[FeatureDeclaration] = []

    def register_field(self, declaration: FieldDeclaration) -> None:
        self._fields.append(declaration)

    def register_feature(self, declaration: FeatureDeclaration) -> None:
        self._features.append(declaration)

    def snapshot(self) -> DeclarationSnapshot:
        """Return an immutable input for one independent compilation."""
        return DeclarationSnapshot(
            fields=tuple(self._fields),
            features=tuple(self._features),
        )


_default_registry: Registry | None = None


def get_default_registry() -> Registry:
    """Create the process-local registry only when an API first needs it."""
    global _default_registry
    if _default_registry is None:
        _default_registry = Registry()
    return _default_registry
