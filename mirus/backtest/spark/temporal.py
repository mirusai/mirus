"""Read warehouse columns and construct intervals for point-in-time joins."""

from pyspark.sql import Window, functions as F

from mirus.payload import PayloadSection


def _column(name):
    return F.col("`" + name.replace("`", "``") + "`")


def read_history(spark, section: PayloadSection):
    columns = [
        _column(spec.dwh_column or name).alias(name)
        for name, spec in section.fields.items()
    ]
    # The base contains initial immutable versions, NOT today's mutable state.
    base = (
        spark.table(section.dwh_table).select(*columns)
        .withColumn("__mirus_from", _column(section.timestamp))
        .withColumn("__mirus_sequence", F.lit(0).cast("long"))
        .withColumn("__mirus_deleted", F.lit(False))
    )
    if section.mutations_table is None:
        return base.withColumn("__mirus_to", F.lit(None).cast("timestamp"))

    # Fixed initial convention: full after-images with the following metadata.
    # createdat stays a business field; update availability is _available_at.
    changes = spark.table(section.mutations_table).select(
        *columns,
        F.col("_available_at").alias("__mirus_from"),
        F.col("_sequence").alias("__mirus_sequence"),
        (F.col("_operation") == "DELETE").alias("__mirus_deleted"),
    )
    versions = base.unionByName(changes)
    timeline = Window.partitionBy(*section.primary_keys).orderBy(
        "__mirus_from", "__mirus_sequence"
    )
    # Build intervals BEFORE removing tombstones so deleted state stays closed.
    return (
        versions.withColumn("__mirus_to", F.lead("__mirus_from").over(timeline))
        .filter(~F.col("__mirus_deleted"))
    )
