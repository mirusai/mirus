"""Compare complete Arrow and pandas scoring jobs on identical cached payloads.

Run: python -m benchmarks.benchmark_spark_udf --features 300 500 --records 20 200
Timings include worker conversion, Python computation, output serialization and
a checksum action. They exclude PIT retrieval and are not online request latency.
"""

import argparse
import json
import platform
import random
from pathlib import Path
from statistics import median
from time import perf_counter

from pyspark.sql import SparkSession, functions as F

from mirus.backtest import compile_offline
from mirus.backtest.spark.config import configure_spark
from mirus.backtest.spark.udf import score_payloads
from mirus.features.decorators import feature, field


def define_features():
    @field(source="loans")
    def dollars(row):
        return (float(row["amount"]) + sum(float(item["amount"]) for item in row["payments"])) / 7

    @feature(source="loans", feature_name="loan_{kind}_{purpose}_{days}d",
             parameters={"kind": range(20), "purpose": range(20), "days": range(1, 31)})
    def amount(rows, *, kind, purpose, days) -> float:
        return sum((row["dollars"] for row in rows
                    if row["kind"] == kind and row["purpose"] == purpose and row["age_days"] < days), 0.0)


def selected_names(count):
    pool = [f"loan_{kind}_{purpose}_{days}d"
            for kind in range(20) for purpose in range(20) for days in range(1, 31)]
    return [pool[index * len(pool) // count] for index in range(count)]


def make_frame(spark, observations, records, partitions):
    driver = spark.range(observations, numPartitions=partitions)
    as_of = F.to_timestamp(F.lit("2026-01-15 12:00:00"))

    def loan(index):
        return F.struct(
            index.alias("loan_id"), ((index + F.col("id")) % 20).alias("kind"),
            ((index * 3 + F.col("id")) % 20).alias("purpose"),
            (index % 40).alias("age_days"), (index * 7).cast("decimal(18,2)").alias("amount"),
            as_of.alias("createdat"),
            F.struct(F.lit(12).alias("term"), F.lit(1).alias("version")).alias("agreement"),
            F.array(F.struct(F.lit(1).cast("decimal(18,2)").alias("amount"),
                             as_of.alias("settled_at"))).alias("payments"),
        )

    indices = F.slice(F.sequence(F.lit(1), F.lit(max(records, 1))), 1, records)
    return driver.select("id", F.struct(
        as_of.alias("as_of"), F.col("id").alias("user_id"),
        F.transform(indices, loan).alias("loans"),
    ).alias("payload"))


def checksum(frame, names):
    # count() alone lets Spark prune the UDF. Hash every output to force execution
    # while collecting just one scalar; decimal summation avoids long overflow.
    hashed = frame.select(F.xxhash64(*[frame[name] for name in names]).alias("hash"))
    return hashed.agg(F.sum(F.col("hash").cast("decimal(38,0)"))).first()[0]


def benchmark(frame, feature_count, repeats, warmups):
    plan = compile_offline(selected_names(feature_count))
    scored = {method: score_payloads(frame, plan, method=method) for method in ("arrow", "pandas")}
    expected = None
    for _ in range(warmups):
        for output in scored.values():
            value = checksum(output, plan.feature_names)
            expected = value if expected is None else expected
            assert value == expected, "Execution methods produced different outputs"
    samples = {method: [] for method in scored}
    randomizer = random.Random(0)
    for _ in range(repeats):
        order = list(scored)
        randomizer.shuffle(order)
        for method in order:
            start = perf_counter()
            value = checksum(scored[method], plan.feature_names)
            samples[method].append((perf_counter() - start) * 1000)
            expected = value if expected is None else expected
            assert value == expected, "Execution methods produced different outputs"
    return {"features": feature_count, "p50_ms": {method: median(values) for method, values in samples.items()},
            "samples_ms": samples, "checksum": str(expected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", nargs="+", type=int, default=[300, 500])
    parser.add_argument("--records", nargs="+", type=int, default=[20, 200])
    parser.add_argument("--observations", type=int, default=128)
    parser.add_argument("--partitions", type=int, default=2)
    parser.add_argument("--batch-rows", type=int, default=32)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not all(0 < count <= 12000 for count in args.features) or min(args.records) < 0:
        parser.error("features must be 1..12000 and records non-negative")
    if min(args.observations, args.partitions, args.batch_rows, args.repeats) < 1 or args.warmups < 0:
        parser.error("observations/partitions/batch-rows/repeats must be positive; warmups non-negative")
    define_features()
    spark = (SparkSession.builder.master(f"local[{args.partitions}]").appName("mirus-udf-benchmark")
             .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", str(args.partitions))
             .getOrCreate())
    configure_spark(spark, batch_rows=args.batch_rows)
    spark.sparkContext.setLogLevel("ERROR")
    import pandas as pd
    import pyarrow as pa

    report = {"python": platform.python_version(), "spark": spark.version,
              "pandas": pd.__version__, "pyarrow": pa.__version__, "platform": platform.platform(),
              "observations": args.observations, "partitions": args.partitions,
              "batch_rows": args.batch_rows, "pool_size": 12000,
              "repeats": args.repeats, "warmups": args.warmups, "runs": [],
              "timing_scope": "Full scoring job on cached nested payloads plus output checksum; excludes PIT retrieval"}
    print(f"Spark {spark.version} | Python {report['python']} | {args.observations} observations", flush=True)
    print(f"{'Records/obs':>11} {'Features':>8} {'Arrow p50 ms':>14} {'Pandas p50 ms':>14}", flush=True)
    try:
        for records in args.records:
            frame = make_frame(spark, args.observations, records, args.partitions).cache()
            try:
                frame.count()  # Materialize input before timing; never cache scored results.
                for count in args.features:
                    run = {"records_per_observation": records, **benchmark(frame, count, args.repeats, args.warmups)}
                    report["runs"].append(run)
                    print(f"{records:>11} {count:>8} {run['p50_ms']['arrow']:>14.1f} {run['p50_ms']['pandas']:>14.1f}", flush=True)
            finally:
                frame.unpersist(blocking=True)
    finally:
        spark.stop()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Saved: {args.output}", flush=True)


if __name__ == "__main__":
    main()
