"""Explicit session settings; importing this module does not create a session."""


def configure_spark(session, *, batch_rows=256):
    """Set UTC and bound Arrow batches for nested payloads on an existing session.

    Batch size counts observations, not nested records; tune for payload size.
    Both UDF paths explicitly use Arrow, without enabling a global UDF override.
    """
    session.conf.set("spark.sql.session.timeZone", "UTC")
    session.conf.set("spark.sql.execution.arrow.maxRecordsPerBatch", batch_rows)
