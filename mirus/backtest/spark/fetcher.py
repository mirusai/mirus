"""Build a nested payload column using Spark joins and aggregations, not UDFs."""

from functools import reduce
from operator import and_

from pyspark.sql import DataFrame, functions as F

from mirus.payload import Payload, PayloadSection

from .temporal import read_history


class SparkFetcher:
    """Assemble historical payloads; feature computation is a separate step."""

    def __init__(self, payload: Payload, session):
        self.payload = payload
        self.spark = session

    def fetch(self, driver: DataFrame) -> DataFrame:
        """Build a lazy payload frame with one row per driver observation."""
        root = self.payload.root
        # A user's observations at different times must remain distinct.
        observation_keys = list(dict.fromkeys(root.primary_keys + [root.timestamp]))
        observations = (
            driver.select(*root.fields)
            .withColumn("__mirus_id", F.to_json(F.struct(*observation_keys)))
            .withColumn("__mirus_as_of", F.col(root.timestamp))
        )

        assembled = self._attach_children(observations, root)
        payload_columns = list(root.fields) + list(root.children)
        return assembled.select(
            *observation_keys,
            F.struct(*payload_columns).alias("payload"),
        )

    def _attach_children(self, parents: DataFrame, parent: PayloadSection) -> DataFrame:
        for child in parent.children.values():
            records = self._join_as_of(parents, child)
            records = self._attach_children(records, child)
            payload_columns = list(child.fields) + list(child.children)

            # Group each branch separately to avoid multiplying sibling lists.
            grouped = records.groupBy("__mirus_parent").agg(
                F.sort_array(
                    F.collect_list(F.struct(
                        F.struct(*child.primary_keys).alias("key"),
                        F.struct(*payload_columns).alias("value"),
                    ))
                ).alias("__mirus_items")
            )
            # Primary-key ordering makes list results deterministic.
            items = F.transform(F.col("__mirus_items"), lambda item: item["value"])
            value = items if child.is_collection else F.element_at(items, 1)
            grouped = grouped.select(
                F.col("__mirus_parent").alias("__mirus_group_id"),
                value.alias(child.name),
            )

            # Retain observations with no matching child records.
            parents = parents.join(
                grouped, parents["__mirus_id"] == grouped["__mirus_group_id"], "left"
            ).drop("__mirus_group_id")
            if child.is_collection:
                parents = parents.withColumn(
                    child.name, F.coalesce(F.col(child.name), F.array())
                )

        return parents

    def _join_as_of(self, parents: DataFrame, section: PayloadSection) -> DataFrame:
        parent = parents.alias("parent")
        source = read_history(self.spark, section).alias("source")
        predicates = [
            parent[key.parent] == source[key.child]
            for key in section.relationship.keys
        ]
        # Version intervals are built before filtering relationship keys, so an
        # old version cannot match its former owner after a relationship changes.
        predicates += [
            source["__mirus_from"] <= parent["__mirus_as_of"],
            source["__mirus_to"].isNull() | (parent["__mirus_as_of"] < source["__mirus_to"]),
        ]

        joined = parent.join(source, reduce(and_, predicates), "inner")
        rows = joined.select(
            parent["__mirus_id"].alias("__mirus_parent"),
            parent["__mirus_as_of"],
            *[source[name] for name in section.fields],
        )

        # Identity includes ancestry, so the same entity in different requests
        # or different branches cannot be mixed during nested aggregation.
        return rows.withColumn(
            "__mirus_id",
            F.to_json(F.struct(
                F.col("__mirus_parent"),
                F.struct(*section.primary_keys).alias("key"),
            )),
        )
