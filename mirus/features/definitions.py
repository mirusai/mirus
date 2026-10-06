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
    parameters: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class DeclarationSnapshot:
    """Immutable view of all declarations collected before compilation."""

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

    fields_by_source: dict[str, tuple[CompiledField, ...]]
    features_by_name: dict[str, CompiledFeature]

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(self.features_by_name)


@dataclass(frozen=True)
class SourceSelection:
    source: str
    fields: tuple[CompiledField, ...]
    features: tuple[CompiledFeature, ...]


@dataclass(frozen=True)
class FeatureDependency:
    """Legacy offline metadata: all fields on the source, not inferred dependencies."""

    feature_name: str
    source: str
    field_names: tuple[str, ...]


@dataclass(frozen=True)
class CompiledSelection:
    features: tuple[CompiledFeature, ...]
    sources: tuple[SourceSelection, ...]
    dependencies: tuple[FeatureDependency, ...]

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)
