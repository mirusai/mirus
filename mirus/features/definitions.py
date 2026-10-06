"""Feature declarations and prepared metadata, with no execution logic."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class FieldDeclaration:
    """One row-level transform exactly as declared by the user."""

    source: str
    name: str
    function: Callable


@dataclass(frozen=True)
class FeatureDeclaration:
    """One feature function and its unexpanded authoring metadata."""

    source: str
    feature_name: str | None
    function: Callable
    # Parameter order is the expansion order. A tuple freezes the choices taken at decoration.
    parameters: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class DeclarationSnapshot:
    """Immutable view of all declarations collected before compilation."""

    # Tuples keep registration order. A list stored here could still be appended to
    # after this frozen snapshot is taken.
    fields: tuple[FieldDeclaration, ...]
    features: tuple[FeatureDeclaration, ...]


@dataclass(frozen=True)
class CompiledField:
    source: str
    name: str
    function: Callable


@dataclass(frozen=True)
class CompiledFeature:
    source: str
    name: str
    function: Callable


@dataclass(frozen=True)
class FeatureCatalog:
    """Prepared definitions; callers must treat the contained mappings as read-only."""

    # Field order within a source is registration order, and the tuple cannot grow.
    fields_by_source: dict[str, tuple[CompiledField, ...]]
    features_by_name: dict[str, CompiledFeature]

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(self.features_by_name)
