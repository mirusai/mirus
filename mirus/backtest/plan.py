"""Spark-free artifact consumed by future distributed-compute adapters."""

from dataclasses import dataclass

from mirus.features.definitions import (
    CompiledFeature,
    CompiledField,
    FeatureDependency,
    SourceSelection,
)


@dataclass(frozen=True)
class OfflinePlan:
    """Concrete callables, dependencies, and types for one offline selection."""

    sources: tuple[SourceSelection, ...]
    feature_names: tuple[str, ...]
    dependencies: tuple[FeatureDependency, ...]
    output_types: tuple[tuple[str, object], ...]

    @property
    def output_schema(self) -> dict[str, object]:
        return dict(self.output_types)

    @property
    def required_sources(self) -> tuple[str, ...]:
        return tuple(source.source for source in self.sources)

    @property
    def fields_by_source(self) -> dict[str, tuple[CompiledField, ...]]:
        return {
            source.source: source.fields
            for source in self.sources
        }

    @property
    def features_by_source(self) -> dict[str, tuple[CompiledFeature, ...]]:
        return {
            source.source: source.features
            for source in self.sources
        }

    @property
    def field_names_by_source(self) -> dict[str, tuple[str, ...]]:
        return {
            source.source: tuple(field.name for field in source.fields)
            for source in self.sources
        }
