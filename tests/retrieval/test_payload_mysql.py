"""An executable example: YAML -> Payload -> nested data from mocked DB rows.

Run after installing mirus: pytest
This exercises the real loader and OnlineFetcher, not a live MySQL database.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mirus.serving import MySQLConnection, OnlineFetcher
from mirus.payload import Field, JoinKey, Payload, PayloadSection, Relationship

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "credit_application.yaml"
EXPECTED = ROOT / "examples" / "credit_application.payload.json"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 10, 1, 9, 0)  # DB timestamps are UTC in this example.


def database_rows():
    """One result batch per table, in depth-first retrieval order."""
    return [
        # lending.loan_record: two loans, with different agreements.
        [
            ("tenant-1", "loan-1", "user-42", "agreement-1", Decimal("10000.00"), CREATED),
            ("tenant-1", "loan-2", "user-42", "agreement-2", Decimal("2500.00"), CREATED),
        ],
        # lending.loan_agreement: these become objects inside each loan.
        [
            ("tenant-1", "agreement-1", 12, Decimal("0.085000"), CREATED),
            ("tenant-1", "agreement-2", 6, Decimal("0.070000"), CREATED),
        ],
        # lending.user_device: independent sibling list, not nested under loans.
        [
            ("tenant-1", "user-42", "device-1", "mobile", CREATED),
            ("tenant-1", "user-42", "device-2", "desktop", CREATED),
        ],
        # lending.login_event: another independent sibling list.
        [
            ("tenant-1", "login-1", "user-42", "192.0.2.10", CREATED),
            ("tenant-1", "login-2", "user-42", "192.0.2.20", CREATED),
        ],
        # lending.third_party_report: a stored report, not an API call.
        [("tenant-1", "report-1", "user-42", "demo_bureau", 720.0, CREATED)],
    ]


def json_value(value):
    """Display adapter only: runtime payloads keep datetime and Decimal values."""
    if isinstance(value, datetime):
        utc = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return utc.isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)  # Do not lose financial precision by casting to float.
    raise TypeError(type(value).__name__)


class TestPayloadDemo:
    def test_mysql_connection_warm_up_configures_serving_session(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value

        MySQLConnection.warm_up(connection)

        assert [call.args[0] for call in cursor.execute.call_args_list] == [
            "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
            "SET time_zone = '+00:00'",
            "SELECT 1",
        ]
        connection.rollback.assert_called_once_with()

    def test_yaml_becomes_one_payload_with_sibling_sections(self):
        payload = Payload.from_yaml(EXAMPLE)
        assert payload.validate() is payload
        assert payload.name == "credit_application"
        assert list(payload.root.children) == [
            "loans", "devices", "login_behavior", "third_party_data",
        ]
        assert all(isinstance(section, PayloadSection)
                   for section in payload.root.children.values())
        assert payload.root.children["loans"].relationship == Relationship(
            "one-to-many", [
                JoinKey("tenant_id", "tenant_id"),
                JoinKey("user_id", "borrower_id"),
            ]
        )
        agreement = payload.root.children["loans"].children["agreement"]
        assert not agreement.is_collection

    def test_ast_normalizes_fields_and_warehouse_mapping(self):
        payload = Payload.from_yaml(EXAMPLE).validate()
        sections = [payload.root]
        while sections:
            section = sections.pop()
            assert all(isinstance(spec, Field) for spec in section.fields.values())
            if section is not payload.root:
                assert isinstance(section.relationship, Relationship)
                assert all(isinstance(key, JoinKey)
                           for key in section.relationship.keys)
            sections.extend(section.children.values())

        loans = payload.root.children["loans"]
        assert loans.fields["principal"].data_type == "decimal(18,2)"
        assert loans.fields["principal"].dwh_column == "loan_amount"
        assert loans.fields["borrower_id"].dwh_column == "customer_id"
        assert loans.fields["loan_id"].dwh_column == "loan_id"
        assert loans.primary_keys == ["tenant_id", "loan_id"]
        assert loans.timestamp == "createdat"
        assert payload.root.fields["as_of"].observation_time
        assert payload.root.relationship is None

    def test_validation_checks_typed_join_references(self):
        payload = Payload.from_yaml(EXAMPLE)
        payload.root.children["devices"].relationship.keys[0].child = "missing_column"
        with pytest.raises(ValueError, match="payload.devices: join keys"):
            payload.validate()

    def test_validation_checks_typed_timestamp_fields(self):
        payload = Payload.from_yaml(EXAMPLE)
        payload.root.fields["as_of"].data_type = "string"
        with pytest.raises(ValueError, match="timestamp marked observation_time"):
            payload.validate()

    def test_fetch_produces_the_expected_nested_payload(self):
        payload = Payload.from_yaml(EXAMPLE).validate()
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = database_rows()

        with patch("mirus.serving.mysql.datetime") as clock:
            clock.now.return_value = NOW
            data = OnlineFetcher(payload, connection).fetch({
                "observation_id": "application-123",
                "tenant_id": "tenant-1",
                "user_id": "user-42",
            })

        # Compare the WHOLE payload, including list ordering and nested parents.
        display = json.loads(json.dumps(data, default=json_value))
        with EXPECTED.open(encoding="utf-8") as file:
            assert display == json.load(file)
        assert isinstance(data["loans"][0]["principal"], Decimal)
        assert isinstance(data["as_of"], datetime)

        queries = [call.args for call in cursor.execute.call_args_list
                   if call.args[0].startswith("SELECT")]
        assert len(queries) == 5  # One read per section, not per loan.
        assert "`tenant_id` = %s AND `borrower_id` = %s" in queries[0][0]
        assert queries[0][1] == (
            "tenant-1", "user-42", NOW.replace(tzinfo=None)
        )
        connection.rollback.assert_called_once()

        print("\nFetched payload (JSON display; dates/decimals serialized):")
        print(json.dumps(display, indent=2))
