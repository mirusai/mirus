"""Payload parsing and nested retrieval from mocked MySQL rows."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mirus.serving import MySQLConnection, OnlineFetcher
from mirus.payload import Field, JoinKey, Payload, PayloadSection, Relationship
from mirus.validation import validate_payload

PAYLOAD_YAML = Path(__file__).resolve().parents[1] / "fixtures/payload.yaml"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 10, 1, 9, 0)  # DB timestamps are UTC.


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


class TestPayloadMySQL:
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
        payload = Payload.from_yaml(PAYLOAD_YAML)
        assert validate_payload(payload) is payload
        assert payload.name == "test_payload"
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
        payload = validate_payload(Payload.from_yaml(PAYLOAD_YAML))
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
        assert loans.db_table == "lending.loan_record"
        assert loans.dwh_table == "lending_history.loan_record"
        assert loans.fields["principal"].data_type == "decimal(18,2)"
        assert loans.fields["principal"].dwh_column == "loan_amount"
        assert loans.fields["borrower_id"].dwh_column == "customer_id"
        assert loans.fields["loan_id"].dwh_column == "loan_id"
        assert loans.primary_keys == ["tenant_id", "loan_id"]
        assert loans.timestamp == "createdat"
        assert payload.root.fields["as_of"].observation_time
        assert payload.root.relationship is None

    def test_validation_checks_typed_join_references(self):
        payload = Payload.from_yaml(PAYLOAD_YAML)
        payload.root.children["devices"].relationship.keys[0].child = "missing_column"
        with pytest.raises(ValueError, match="payload.devices: join keys"):
            validate_payload(payload)

    def test_validation_checks_typed_timestamp_fields(self):
        payload = Payload.from_yaml(PAYLOAD_YAML)
        payload.root.fields["as_of"].data_type = "string"
        with pytest.raises(ValueError, match="timestamp marked observation_time"):
            validate_payload(payload)

    def test_fetch_produces_the_expected_nested_payload(self):
        payload = validate_payload(Payload.from_yaml(PAYLOAD_YAML))
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

        # Compare native values, including sibling lists and each nested parent.
        assert data == {
            "observation_id": "application-123", "tenant_id": "tenant-1",
            "user_id": "user-42", "as_of": NOW.replace(tzinfo=None),
            "loans": [
                {"tenant_id": "tenant-1", "loan_id": f"loan-{index}",
                 "borrower_id": "user-42", "agreement_id": f"agreement-{index}",
                 "principal": Decimal(principal), "createdat": CREATED,
                 "agreement": {"tenant_id": "tenant-1", "agreement_id": f"agreement-{index}",
                               "term_months": term, "interest_rate": Decimal(rate),
                               "createdat": CREATED}}
                for index, principal, term, rate in [
                    (1, "10000.00", 12, "0.085000"), (2, "2500.00", 6, "0.070000")
                ]
            ],
            "devices": [
                {"tenant_id": "tenant-1", "user_id": "user-42", "device_id": f"device-{index}",
                 "device_type": kind, "createdat": CREATED}
                for index, kind in [(1, "mobile"), (2, "desktop")]
            ],
            "login_behavior": [
                {"tenant_id": "tenant-1", "login_id": f"login-{index}", "user_id": "user-42",
                 "ip_address": address, "createdat": CREATED}
                for index, address in [(1, "192.0.2.10"), (2, "192.0.2.20")]
            ],
            "third_party_data": [
                {"tenant_id": "tenant-1", "report_id": "report-1", "user_id": "user-42",
                 "provider": "demo_bureau", "score": 720.0, "createdat": CREATED}
            ],
        }
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
