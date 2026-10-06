"""Spark-free artifact consumed by future distributed-compute adapters."""

from dataclasses import dataclass

from mirus.features.definitions import CompiledFeature, CompiledField, FeatureCatalog


@dataclass(frozen=True)
class OfflinePlan:
    """Prepared catalog and declared output types for one offline selection."""

    catalog: FeatureCatalog
    output_types: tuple[tuple[str, object], ...]

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self.catalog.feature_names

    @property
    def output_schema(self) -> dict[str, object]:
        return dict(self.output_types)

    @property
    def required_sources(self) -> tuple[str, ...]:
        return tuple(self.catalog.fields_by_source)

    @property
    def fields_by_source(self) -> dict[str, tuple[CompiledField, ...]]:
        return self.catalog.fields_by_source

    @property
    def features_by_source(self) -> dict[str, tuple[CompiledFeature, ...]]:
        grouped: dict[str, list[CompiledFeature]] = {}
        for feature in self.catalog.features_by_name.values():
            grouped.setdefault(feature.source, []).append(feature)
        return {source: tuple(features) for source, features in grouped.items()}

    @property
    def field_names_by_source(self) -> dict[str, tuple[str, ...]]:
        return {
            source: tuple(field.name for field in fields)
            for source, fields in self.catalog.fields_by_source.items()
        }
