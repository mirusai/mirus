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
    # Choices are snapshotted at decoration, preserving parameter order.
    parameters: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class FeatureCatalog:
    """Ready-to-execute metadata; treat the contained mappings as read-only."""

    fields_by_source: dict[str, dict[str, Callable]]
    features_by_source: dict[str, dict[str, Callable]]
    # Ordered name index; source groups reference the same bound callables.
    features_by_name: dict[str, tuple[str, Callable]]
