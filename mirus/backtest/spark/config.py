"""Recommended settings; users apply and override them on their Spark session."""


RECOMMENDED_SPARK_CONFIG = {
    "spark.sql.session.timeZone": "UTC",
    # Batch size counts observations, not nested records; tune for payload size.
    "spark.sql.execution.arrow.maxRecordsPerBatch": "256",
    "spark.sql.adaptive.enabled": "true",
    "spark.sql.adaptive.coalescePartitions.enabled": "true",
    "spark.sql.adaptive.skewJoin.enabled": "true",
}
