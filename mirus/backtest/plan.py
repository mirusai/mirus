"""Spark-free artifact consumed by distributed-compute adapters."""

from dataclasses import dataclass
from functools import partial
from typing import Callable, get_type_hints

from mirus.features.definitions import FeatureCatalog


@dataclass(frozen=True)
class OfflinePlan:
    """Prepared catalog with derived metadata for one offline selection."""

    catalog: FeatureCatalog

    @property
    def output_types(self) -> tuple[tuple[str, object], ...]:
        """Resolve selected return annotations when building the output schema."""
        outputs = []
        for name, (_, function) in self.catalog.features_by_name.items():
            # Parameterized features bind arguments with partial; annotations
            # belong to the original user function.
            while isinstance(function, partial):
                function = function.func

            hints = get_type_hints(function)
            if "return" not in hints:
                raise TypeError(f"{function.__name__} needs a return annotation")
            outputs.append((name, hints["return"]))

        return tuple(outputs)

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(self.catalog.features_by_name)

    @property
    def output_schema(self) -> dict[str, object]:
        return dict(self.output_types)

    @property
    def required_sources(self) -> tuple[str, ...]:
        return tuple(self.catalog.fields_by_source)

    @property
    def fields_by_source(self) -> dict[str, dict[str, Callable]]:
        return self.catalog.fields_by_source

    @property
    def features_by_source(self) -> dict[str, dict[str, Callable]]:
        return self.catalog.features_by_source

    @property
    def field_names_by_source(self) -> dict[str, tuple[str, ...]]:
        return {
            source: tuple(fields)
            for source, fields in self.catalog.fields_by_source.items()
        }
