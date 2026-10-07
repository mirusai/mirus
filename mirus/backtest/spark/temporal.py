"""Read warehouse columns and construct intervals for point-in-time joins."""

from pyspark.sql import Window, functions as F

from mirus.payload import PayloadSection


def _column(name):
    """Treat mapped warehouse names literally, including dots and backticks."""
    return F.col("`" + name.replace("`", "``") + "`")


def read_history(spark, section: PayloadSection):
    """Read immutable records or complete row versions using the YAML mapping."""
    columns = [
        _column(spec.dwh_column or name).alias(name)
        for name, spec in section.fields.items()
    ]

    # Mutable sources point dwh at complete historical row versions.
    versions = (
        spark.table(section.dwh_table).select(*columns)
        .withColumn("__mirus_from", _column(section.timestamp))
    )
    if not section.mutable:
        return versions.withColumn("__mirus_to", F.lit(None).cast("timestamp"))

    # [version time, next version time) selects the latest available row per key
    # without joining every old version to each observation. Key/time is unique.
    timeline = Window.partitionBy(*section.primary_keys).orderBy("__mirus_from")
    return versions.withColumn("__mirus_to", F.lead("__mirus_from").over(timeline))
