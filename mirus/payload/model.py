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
    mutations_table: str | None = None
    relationship: Relationship | None = None
    children: dict[str, PayloadSection] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def _from_definition(cls, name: str, definition: dict) -> PayloadSection:
        settings = {"source", "fields", "db", "dwh", "relationship", "field_mapping"}
        mapping = definition.get("field_mapping", {})
        if set(mapping) - set(definition["fields"]):
            raise ValueError(f"{name}: field_mapping refers to undeclared fields")
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
            db_table=definition.get("db", {}).get("table"),
            dwh_table=definition.get("dwh", {}).get("table"),
            mutations_table=definition.get("dwh", {}).get("mutations_table"),
            relationship=Relationship(
                cardinality=relationship["type"],
                keys=[JoinKey(parent, child) for parent, child in relationship["on"].items()],
            ) if relationship else None,
            children={name: cls._from_definition(name, child)
                      for name, child in definition.items() if name not in settings},
            source=definition.get("source"),
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
    """The single public definition passed to OnlineFetcher or OfflineFetcher."""

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

    def validate(self) -> Payload:
        """Check definition structure only, without querying either backend.

        Database schemas, data types at runtime, cardinality and completeness
        checks are intentionally left for a later implementation pass.
        """
        if not self.name or type(self.version) is not int or self.version < 1:
            raise ValueError("Payload needs a name and a positive integer version")
        if self.root.source != "request":
            raise ValueError("payload.source must be request")

        def visit(section, parent=None, path="payload"):
            if not section.fields or not section.primary_keys:
                raise ValueError(f"{path}: fields and a primary key are required")
            marker = "observation_time" if parent is None else "available_at"
            other = "available_at" if parent is None else "observation_time"
            times = [spec for spec in section.fields.values() if getattr(spec, marker)]
            if len(times) != 1 or times[0].data_type != "timestamp":
                raise ValueError(f"{path}: one timestamp marked {marker} is required")
            if any(getattr(spec, other) for spec in section.fields.values()):
                raise ValueError(f"{path}: unexpected {other} marker")
            if set(section.fields) & set(section.children):
                raise ValueError(f"{path}: field and child names must be different")
            if parent is not None:
                if not (section.db_table or section.dwh_table):
                    raise ValueError(f"{path}: a DB or DWH table is required")
                relationship = section.relationship
                if relationship is None or relationship.cardinality not in {"one-to-one", "one-to-many", "many-to-one"}:
                    raise ValueError(f"{path}: unsupported relationship type")
                if not relationship.keys or any(
                    key.parent not in parent.fields or key.child not in section.fields
                    for key in relationship.keys
                ):
                    raise ValueError(f"{path}: join keys must exist on the parent and child")
            for name, child in section.children.items():
                visit(child, section, f"{path}.{name}")

        visit(self.root)
        return self
