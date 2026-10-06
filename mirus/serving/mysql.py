"""Translate a Payload into batched MySQL reads and a nested dictionary."""

from collections import defaultdict
from datetime import datetime, timezone
from importlib import import_module

from mirus.payload import Payload, PayloadSection


def _quote(name: str) -> str:
    """Quote a possibly schema-qualified MySQL identifier."""
    return ".".join("`" + part.replace("`", "``") + "`" for part in name.split("."))


class MySQLConnection:
    """Build and warm a PyMySQL connection for online payload serving."""

    def __init__(self, **connect_options):
        """Store options that will be forwarded to ``pymysql.connect``."""
        self.connect_options = connect_options

    def connect(self):
        """Open, warm, and return a connection ready to serve requests."""
        # Keep PyMySQL optional until a MySQL connection is actually requested.
        try:
            pymysql = import_module("pymysql")
        except ImportError as error:
            raise RuntimeError(
                "PyMySQL is required; install mirus[mysql]"
            ) from error

        # Never return a partially initialized connection to the caller.
        connection = pymysql.connect(**self.connect_options)
        try:
            self.warm_up(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def warm_up(connection) -> None:
        """Configure the session and exercise it before serving requests."""
        with connection.cursor() as cursor:
            # Isolation and UTC are session settings, so they run once per
            # connection rather than adding round trips to every request.
            cursor.execute(
                "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ"
            )
            cursor.execute("SET time_zone = '+00:00'")

            # Exercise the established connection before it enters the pool.
            cursor.execute("SELECT 1")

        # SELECT 1 may open a transaction when autocommit is disabled.
        connection.rollback()


class MySQLFetcher:
    """Fetch one payload from a warmed, request-exclusive MySQL connection."""

    def __init__(self, payload: Payload, connection):
        """Bind a payload definition to a caller-owned connection."""
        # The caller owns this dedicated, warmed connection.
        # Do not share it between concurrent requests or active transactions.
        self.payload = payload
        self.connection = connection

    def fetch(self, request: dict) -> dict:
        """Fetch and assemble a payload from one consistent database snapshot."""
        # Online requests always use the current UTC time as their observation
        # time; a caller-supplied value cannot override it.
        root = self.payload.root
        as_of = datetime.now(timezone.utc).replace(tzinfo=None)
        values = {**request, root.timestamp: as_of}
        result = {name: values[name] for name in root.fields}

        # One snapshot keeps all recursive section reads mutually consistent.
        # The cursor context closes only the cursor, not the transaction.
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
                )
                self._attach_children(cursor, root, [result], as_of)
        finally:
            # End the snapshot on both success and failure before this
            # connection can be reused by another request.
            self.connection.rollback()

        return result

    def _attach_children(
        self, cursor, parent: PayloadSection, records: list[dict], as_of
    ):
        """Read every child section and attach it to its parent records."""
        for child in parent.children.values():
            # Deduplicate complete join keys so each section is fetched in one
            # batched query rather than one query per parent record.
            parent_fields = [key.parent for key in child.relationship.keys]
            child_fields = [key.child for key in child.relationship.keys]
            keys = {tuple(record[name] for name in parent_fields) for record in records}
            keys = [key for key in keys if all(value is not None for value in key)]

            # Build deeper descendants before attaching these rows to parents.
            rows = self._read(cursor, child, child_fields, keys, as_of) if keys else []
            self._attach_children(cursor, child, rows, as_of)

            # Index child rows by their relationship key, then preserve the
            # relationship's collection-versus-object shape.
            grouped = defaultdict(list)
            for row in rows:
                grouped[tuple(row[name] for name in child_fields)].append(row)
            for record in records:
                matches = grouped[tuple(record[name] for name in parent_fields)]
                record[child.name] = (
                    matches if child.is_collection
                    else (matches[0] if matches else None)
                )

    def _read(
        self, cursor, section: PayloadSection, join_fields, keys, as_of
    ) -> list[dict]:
        """Read one section for a batch of relationship keys."""
        # Quote identifiers but parameterize all values.
        columns = list(section.fields)
        key_clause = " AND ".join(f"{_quote(name)} = %s" for name in join_fields)
        matches = " OR ".join(f"({key_clause})" for _ in keys)
        sql = (
            f"SELECT {', '.join(_quote(name) for name in columns)} "
            f"FROM {_quote(section.db_table)} WHERE ({matches}) "
            f"AND {_quote(section.timestamp)} <= %s "
            f"ORDER BY {', '.join(_quote(name) for name in section.primary_keys)}"
        )
        parameters = tuple(value for key in keys for value in key) + (as_of,)
        cursor.execute(sql, parameters)

        # Standard tuple cursors and PyMySQL DictCursor are both supported.
        return [dict(row) if isinstance(row, dict) else dict(zip(columns, row))
                for row in cursor.fetchall()]
