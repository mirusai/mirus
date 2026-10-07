"""Optional project-definition checks for development and CI, not serving."""

from pathlib import Path

from mirus.payload import Payload
from .features import validate_features
from .payload import validate_payload

__all__ = ["validate_project", "validate_payload", "validate_features"]


def validate_project(yaml_path: str | Path) -> Payload:
    """Validate YAML and all imported declarations; return the payload contract.

    Import the project's feature modules first. This does not discover modules,
    query backends, execute feature code, or configure the serving process.
    """
    payload = validate_payload(Payload.from_yaml(yaml_path))
    validate_features(payload)
    return payload
