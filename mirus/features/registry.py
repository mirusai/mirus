"""Process-local storage for uncompiled feature declarations."""

from .definitions import FeatureDeclaration, FieldDeclaration


class Registry:
    """Collect declarations without validation or runtime compilation."""

    def __init__(self) -> None:
        self._fields: list[FieldDeclaration] = []
        self._features: list[FeatureDeclaration] = []

    def register_field(self, declaration: FieldDeclaration) -> None:
        self._fields.append(declaration)

    def register_feature(self, declaration: FeatureDeclaration) -> None:
        self._features.append(declaration)

    def snapshot(self) -> tuple[tuple[FieldDeclaration, ...], tuple[FeatureDeclaration, ...]]:
        """Copy both declaration lists for independent preparation or validation."""
        # Copy the live lists. Later registrations append to the registry, not to this snapshot.
        return tuple(self._fields), tuple(self._features)


default_registry = Registry()
