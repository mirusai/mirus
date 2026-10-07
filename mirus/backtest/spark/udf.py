"""Execute a prepared catalog with either Arrow scalar or pandas batch UDFs."""

from datetime import date, datetime
from types import UnionType
from typing import Union, get_args, get_origin

import numpy as np
import pandas as pd
from pyspark.sql import DataFrame, functions as F, types as T

from mirus.backtest.plan import OfflinePlan
from mirus.features.compute import compute_features


def _spark_type(annotation: object) -> T.DataType:
    if get_origin(annotation) in (Union, UnionType):
        members = [member for member in get_args(annotation) if member is not type(None)]
        if len(members) == 1:
            return _spark_type(members[0])

    scalar_types = {
        int: T.LongType,
        float: T.DoubleType,
        bool: T.BooleanType,
        str: T.StringType,
        bytes: T.BinaryType,
        date: T.DateType,
        datetime: T.TimestampType,
    }
    if annotation not in scalar_types:
        raise TypeError(f"Unsupported feature output type: {annotation!r}")
    return scalar_types[annotation]()


def _normalize(value):
    """Normalize pandas containers only; do not reinterpret source data types."""
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, list):
        # Converting the outer array does not normalize its nested values.
        return [_normalize(item) for item in value]

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.generic):
        return value.item()
    return None if value is pd.NaT else value


def score_payloads(
    frame: DataFrame, plan: OfflinePlan, *, method: str = "arrow",
) -> DataFrame:
    """Return observation columns plus selected features; no Spark action is run.

    Input has a non-null `payload` struct. Both paths serialize the prepared
    catalog, never the Spark session, frame, YAML or registry. Spark/Arrow scalar
    coercion is unchanged; large nullable integers may lose precision.
    """
    if method not in ("arrow", "pandas"):
        raise ValueError("method must be 'arrow' or 'pandas'")

    names = plan.feature_names
    if not names:
        return frame.drop("payload")

    # Resolve annotations on the driver, not for every payload on the workers.
    schema = T.StructType([
        T.StructField(name, _spark_type(annotation), nullable=True)
        for name, annotation in plan.output_types
    ])
    catalog = plan.catalog

    if method == "arrow":
        def score(payload):
            return compute_features(payload.asDict(recursive=True), catalog=catalog)

        score_udf = F.udf(score, schema, useArrow=True)
        payload_column = F.col("payload")
    else:
        def score(batch: pd.DataFrame) -> pd.DataFrame:
            # Batch transport still calls the same Python logic per payload.
            results = [
                compute_features(_normalize(payload), catalog=catalog)
                for payload in batch["value"]
            ]
            return pd.DataFrame(results, columns=names)

        score_udf = F.pandas_udf(score, schema)
        # A one-column batch already contains each full payload as a dict.
        payload_column = F.struct(F.col("payload").alias("value"))

    # Compute all selected features in one UDF, then expand the struct in Spark.
    observation_columns = [frame[name] for name in frame.columns if name != "payload"]
    scored = frame.select(
        *observation_columns, score_udf(payload_column).alias("__mirus_features")
    )
    return scored.select(*observation_columns, "__mirus_features.*")
