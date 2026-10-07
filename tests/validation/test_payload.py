"""Validate contract structure independently of either retrieval backend."""

from pathlib import Path

import pytest

from mirus.payload import Field, Payload, PayloadSection
from mirus.validation import validate_payload


@pytest.fixture
def payload():
    return Payload.from_yaml(Path(__file__).resolve().parents[1] / "fixtures/payload.yaml")


@pytest.mark.parametrize("attribute,value,message", [
    ("name", "", "name"), ("version", 0, "positive integer"),
    ("version", True, "positive integer"),
])
def test_invalid_payload_identity(payload, attribute, value, message):
    setattr(payload, attribute, value)
    with pytest.raises(ValueError, match=message):
        validate_payload(payload)


def test_primary_key_and_timestamp_are_required(payload):
    payload.root.fields["observation_id"].primary_key = False
    with pytest.raises(ValueError, match="primary key"):
        validate_payload(payload)
    payload.root.fields["observation_id"].primary_key = True
    payload.root.fields["second_time"] = Field("timestamp", observation_time=True)
    with pytest.raises(ValueError, match="one timestamp"):
        validate_payload(payload)


def test_field_and_child_names_cannot_overlap(payload):
    payload.root.fields["loans"] = Field("string")
    with pytest.raises(ValueError, match="field and child names"):
        validate_payload(payload)


def test_relationship_and_table_are_required_for_child(payload):
    loans = payload.root.children["loans"]
    loans.db_table = loans.dwh_table = None
    with pytest.raises(ValueError, match="DB or DWH table"):
        validate_payload(payload)
    loans.db_table = "loans"
    loans.relationship.cardinality = "unknown"
    with pytest.raises(ValueError, match="unsupported relationship"):
        validate_payload(payload)


def test_mapping_requires_declared_field(payload):
    payload.root.children["loans"] = PayloadSection._from_definition("loans", {
        "fields": {"id": {"type": "string", "primary_key": True},
                   "createdat": {"type": "timestamp", "available_at": True}},
        "field_mapping": {"missing": "warehouse_id"},
    })
    with pytest.raises(ValueError, match="undeclared fields"):
        validate_payload(payload)


def test_valid_nested_contract_returns_same_object(payload):
    assert validate_payload(payload) is payload
    assert payload.root.children["loans"].field_mapping["principal"] == "loan_amount"


def test_root_must_be_request_source(payload):
    payload.root.source = "table"
    with pytest.raises(ValueError, match="payload.source must be request"):
        validate_payload(payload)


@pytest.mark.parametrize("path", ["loans", "agreement"])
def test_nested_join_keys_must_reference_parent_and_child(payload, path):
    section = payload.root.children["loans"]
    if path == "agreement":
        section = section.children[path]
    section.relationship.keys[0].parent = "missing_column"
    with pytest.raises(ValueError, match="join keys must exist"):
        validate_payload(payload)


def test_child_cannot_use_observation_time_marker(payload):
    payload.root.children["loans"].fields["createdat"].observation_time = True
    with pytest.raises(ValueError, match="unexpected observation_time"):
        validate_payload(payload)


def test_loader_preserves_on_key_without_changing_global_yaml_resolvers(payload):
    import yaml

    assert payload.root.children["loans"].relationship.keys[1].child == "borrower_id"
    assert payload.root.fields["observation_id"].primary_key is True
    assert yaml.safe_load("on: true") == {True: True}


@pytest.mark.parametrize("value", ["false", 1, None])
def test_mutable_requires_a_boolean(payload, value):
    payload.root.children["loans"].mutable = value
    with pytest.raises(ValueError, match="mutable must be a boolean"):
        validate_payload(payload)


def test_mutable_source_requires_a_historical_warehouse_table(payload):
    loans = payload.root.children["loans"]
    loans.mutable = True
    loans.dwh_table = None
    with pytest.raises(ValueError, match="historical DWH table"):
        validate_payload(payload)


@pytest.mark.parametrize("setting, expected", [("", False), ("mutable: false", False), ("mutable: true", True)])
def test_yaml_mutable_setting_is_metadata_not_a_payload_child(tmp_path, setting, expected):
    yaml_file = tmp_path / "payload.yaml"
    yaml_file.write_text(f"""name: demo
version: 1
payload:
  source: request
  fields:
    user_id: {{type: string, primary_key: true}}
    as_of: {{type: timestamp, observation_time: true}}
  loans:
    {setting}
    dwh: loan_versions
    relationship:
      type: one-to-many
      on:
        user_id: user_id
    fields:
      loan_id: {{type: string, primary_key: true}}
      user_id: {{type: string}}
      updatedat: {{type: timestamp, available_at: true}}
    agreement:
      mutable: false
      dwh: agreements
      relationship:
        type: one-to-one
        on:
          loan_id: loan_id
      fields:
        loan_id: {{type: string, primary_key: true}}
        createdat: {{type: timestamp, available_at: true}}
""", encoding="utf-8")

    payload = validate_payload(Payload.from_yaml(yaml_file))
    loans = payload.root.children["loans"]

    assert loans.mutable is expected
    assert loans.db_table is None
    assert loans.dwh_table == "loan_versions"
    assert loans.timestamp == "updatedat"
    assert list(loans.children) == ["agreement"]
    assert loans.children["agreement"].mutable is False
    assert loans.children["agreement"].dwh_table == "agreements"
