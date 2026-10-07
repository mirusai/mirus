"""Validate the payload AST without querying DB or warehouse schemas."""

from mirus.payload import Payload, PayloadSection


def _validate_section(section: PayloadSection, parent: PayloadSection | None, path: str):
    if type(section.mutable) is not bool:
        raise ValueError(f"{path}: mutable must be a boolean")
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
    if set(section.field_mapping) - set(section.fields):
        raise ValueError(f"{path}: field_mapping refers to undeclared fields")
    if parent is not None:
        if not (section.db_table or section.dwh_table):
            raise ValueError(f"{path}: a DB or DWH table is required")
        if section.mutable and not section.dwh_table:
            raise ValueError(f"{path}: mutable sources require a historical DWH table")
        relationship = section.relationship
        if relationship is None or relationship.cardinality not in {"one-to-one", "one-to-many", "many-to-one"}:
            raise ValueError(f"{path}: unsupported relationship type")
        if not relationship.keys or any(
            key.parent not in parent.fields or key.child not in section.fields
            for key in relationship.keys
        ):
            raise ValueError(f"{path}: join keys must exist on the parent and child")
    for name, child in section.children.items():
        _validate_section(child, section, f"{path}.{name}")


def validate_payload(payload: Payload) -> Payload:
    """Check static contract structure, not runtime data or backend schemas."""
    if not payload.name or type(payload.version) is not int or payload.version < 1:
        raise ValueError("Payload needs a name and a positive integer version")
    if payload.root.source != "request":
        raise ValueError("payload.source must be request")
    _validate_section(payload.root, None, "payload")
    return payload
