"""Definition checks are explicit development-time work, never feature execution."""

from pathlib import Path

import pytest

from mirus.features.compute import compute_features, prepare_features
from mirus.features.decorators import feature, field
from mirus.payload import Payload
from mirus.validation import validate_features, validate_project

pytestmark = pytest.mark.usefixtures("isolated_feature_registry")
PAYLOAD_YAML = Path(__file__).resolve().parents[1] / "fixtures/payload.yaml"


@pytest.fixture
def payload():
    return Payload.from_yaml(PAYLOAD_YAML)


def test_project_validates_without_executing_or_replacing_prepared_catalog():
    @field(source="loans")
    def dollars(row):
        raise AssertionError("Validation executed a field")

    @feature(source="loans", feature_name="amount_{days}", parameters={"days": [30, 90]})
    def amount(rows, *, days) -> float | None:
        raise AssertionError("Validation executed a feature")

    catalog = prepare_features(["amount_30"])
    assert validate_project(PAYLOAD_YAML).name == "test_payload"
    assert tuple(catalog.features_by_name) == ("amount_30",)


def test_project_without_imported_features_can_validate_payload_only():
    assert validate_project(PAYLOAD_YAML).root.children["loans"].is_collection


def test_project_checks_payload_before_feature_definitions(tmp_path):
    yaml_path = tmp_path / "payload.yaml"
    yaml_path.write_text(PAYLOAD_YAML.read_text().replace("version: 1", "version: 0"))
    @feature(source="missing")
    def invalid(rows):
        raise AssertionError("Validation executed user code")

    with pytest.raises(ValueError, match="positive integer"):
        validate_project(yaml_path)


def test_duplicate_fields_are_scoped_to_source(payload):
    @field(source="loans")
    def amount(row):
        return row["amount"]

    field(source="devices")(amount)  # Same field name on another source is valid.
    validate_features(payload)
    field(source="loans")(amount)
    with pytest.raises(ValueError, match="Duplicate field"):
        validate_features(payload)


def test_duplicate_feature_names_are_global_and_preserve_old_catalog(payload):
    @feature(source="loans", feature_name="count")
    def first(rows) -> int:
        return len(rows)

    catalog = prepare_features()

    @feature(source="devices", feature_name="count")
    def second(rows) -> int:
        return 999

    with pytest.raises(ValueError, match="Duplicate feature"):
        validate_features(payload)
    assert compute_features({"loans": [{}]}, catalog=catalog) == {"count": 1}


@pytest.mark.parametrize("template", ["amount", "amount_{days}d"])
def test_expanded_feature_names_must_be_unique(payload, template):
    @feature(source="loans", feature_name="amount_90d")
    def existing(rows) -> float:
        return 0.0

    @feature(source="loans", feature_name=template, parameters={"days": [30, 90]})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(ValueError, match="Duplicate feature"):
        validate_features(payload)


@pytest.mark.parametrize("choices", [[], "30d", b"30d", 30])
def test_parameter_choices_must_be_nonempty_sequences(payload, choices):
    @feature(source="loans", feature_name="amount_{days}", parameters={"days": choices})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(ValueError, match="non-empty sequence"):
        validate_features(payload)


def test_parameterized_features_require_a_template(payload):
    @feature(source="loans", parameters={"days": [30]})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(ValueError, match="feature_name template"):
        validate_features(payload)


def test_template_references_declared_parameters(payload):
    @feature(source="loans", feature_name="amount_{window}", parameters={"days": [30]})
    def amount(rows, *, days) -> float:
        return float(days)

    with pytest.raises(KeyError, match="window"):
        validate_features(payload)


@pytest.mark.parametrize("parameters", [None, {"days": [30]}, {"unknown": [30]}])
def test_signature_requires_all_arguments_and_matching_parameters(payload, parameters):
    @feature(source="loans", feature_name="amount_{days}" if parameters else "amount", parameters=parameters)
    def amount(rows, *, days, purpose) -> float:
        raise AssertionError("Invalid feature executed")

    with pytest.raises(TypeError, match="cannot bind declared parameters"):
        validate_features(payload)


def test_parameters_cannot_override_rows(payload):
    @feature(source="loans", feature_name="amount_{rows}", parameters={"rows": [30]})
    def amount(rows) -> float:
        raise AssertionError("Invalid feature executed")

    with pytest.raises(TypeError, match="multiple values"):
        validate_features(payload)


def test_fields_accept_one_positional_record(payload):
    @field(source="loans")
    def invalid(row, required):
        raise AssertionError("Invalid field executed")

    with pytest.raises(TypeError, match="missing a required argument"):
        validate_features(payload)


@pytest.mark.parametrize("decorator", [feature, field])
@pytest.mark.parametrize("source", ["laons", "loans.agreement", "agreement"])
def test_sources_must_match_top_level_sections(payload, decorator, source):
    @decorator(source=source)
    def value(rows) -> int:
        raise AssertionError("Validation executed user code")

    with pytest.raises(ValueError, match="Unknown feature source"):
        validate_features(payload)


def test_project_checks_declarations_not_selected_for_serving(payload):
    @feature(source="loans")
    def valid(rows) -> int:
        return len(rows)

    @feature(source="loans", feature_name="invalid_{days}", parameters={"days": []})
    def invalid(rows, *, days) -> int:
        raise AssertionError("Unselected feature executed")

    assert compute_features({"loans": []}, ["valid"]) == {"valid": 0}
    with pytest.raises(ValueError, match="non-empty sequence"):
        validate_features(payload)


def test_features_require_return_annotations_for_shared_offline_use(payload):
    @feature(source="loans")
    def untyped(rows):
        raise AssertionError("Validation executed user code")

    with pytest.raises(TypeError, match="return annotation"):
        validate_features(payload)


def test_return_annotations_must_resolve(payload):
    @feature(source="loans")
    def invalid(rows) -> "MissingOutputType":
        raise AssertionError("Validation executed user code")

    with pytest.raises(NameError, match="MissingOutputType"):
        validate_features(payload)
