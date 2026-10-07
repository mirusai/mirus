"""Shared payload contract for serving and backtest fetchers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Field:
    data_type: str
    primary_key: bool = False
    available_at: bool = False
    observation_time: bool = False
    dwh_column: str | None = None


@dataclass
class JoinKey:
    parent: str
    child: str


@dataclass
class Relationship:
    cardinality: str
    keys: list[JoinKey]


@dataclass
class PayloadSection:
    name: str
    fields: dict[str, Field]
    db_table: str | None = None
    dwh_table: str | None = None
    mutable: bool = False  # Mutable sources require complete DWH row-version history.
    relationship: Relationship | None = None
    children: dict[str, PayloadSection] = field(default_factory=dict)
    source: str | None = None
    field_mapping: dict[str, str] = field(default_factory=dict)

    @classmethod
    def _from_definition(cls, name: str, definition: dict) -> PayloadSection:
        settings = {"source", "fields", "db", "dwh", "mutable", "relationship", "field_mapping"}
        mapping = definition.get("field_mapping", {})
        relationship = definition.get("relationship")
        return cls(
            name=name,
            fields={
                name: Field(
                    data_type=spec["type"],
                    primary_key=spec.get("primary_key", False),
                    available_at=spec.get("available_at", False),
                    observation_time=spec.get("observation_time", False),
                    dwh_column=mapping.get(name, name),
                )
                for name, spec in definition["fields"].items()
            },
            db_table=definition.get("db"),
            dwh_table=definition.get("dwh"),
            mutable=definition.get("mutable", False),
            relationship=Relationship(
                cardinality=relationship["type"],
                keys=[JoinKey(parent, child) for parent, child in relationship["on"].items()],
            ) if relationship else None,
            children={name: cls._from_definition(name, child)
                      for name, child in definition.items() if name not in settings},
            source=definition.get("source"),
            field_mapping=dict(mapping),
        )

    @property
    def primary_keys(self) -> list[str]:
        return [name for name, spec in self.fields.items() if spec.primary_key]

    @property
    def timestamp(self) -> str:
        return next(name for name, spec in self.fields.items()
                    if spec.available_at or spec.observation_time)

    @property
    def is_collection(self) -> bool:
        return self.relationship is not None and self.relationship.cardinality == "one-to-many"


@dataclass
class Payload:
    """Shared payload contract consumed by online and historical retrieval."""

    name: str
    version: int
    root: PayloadSection

    @classmethod
    def from_yaml(cls, path: str | Path) -> Payload:
        import re
        import yaml

        # SafeLoader uses YAML 1.1 booleans, which turn the key "on" into True.
        # Keep a local safe loader with true/false-only boolean resolution.
        class Loader(yaml.SafeLoader):
            pass

        Loader.yaml_implicit_resolvers = {
            key: [(tag, pattern) for tag, pattern in resolvers
                  if tag != "tag:yaml.org,2002:bool"]
            for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
        }
        Loader.add_implicit_resolver(
            "tag:yaml.org,2002:bool",
            re.compile(r"^(?:true|false)$", re.IGNORECASE),
            list("tTfF"),
        )
        with Path(path).open(encoding="utf-8") as file:
            definition = yaml.load(file, Loader=Loader)
        return cls(
            name=definition["name"],
            version=definition["version"],
            root=PayloadSection._from_definition("payload", definition["payload"]),
        )
