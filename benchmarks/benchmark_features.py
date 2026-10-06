"""Compare direct Python aggregation with equivalent framework computation.

Run: python -m benchmarks.benchmark_features --rows 1000 10000 50000
"""

import argparse
import gc
import json
import math
import platform
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from mirus.features.compute import compile_features, compute_features
from examples.parameterized_features import dollar_amount, loan_amount

FEATURE_NAMES = ["loan_credit_shopping_amount_30d", "loan_home_medical_amount_90d"]
RUNNER = compile_features(FEATURE_NAMES)


def make_payload(count):
    return {"loans": [
        {"amount": 700 + index % 1000,
         "loan_type": ("credit", "home", "private")[index % 3],
         "purpose": ("shopping", "medical")[index % 2],
         "age_days": index % 120}
        for index in range(count)
    ]}


def prepare_rows(payload):
    # Same field function and shallow-copy semantics, written directly for this task.
    return [{**row, "dollar_amount": dollar_amount(row)} for row in payload["loans"]]


def raw_aggregations(rows):
    # Decorators leave the original function callable directly.
    return {
        FEATURE_NAMES[0]: loan_amount(rows, loan_type="credit", purpose="shopping", days=30),
        FEATURE_NAMES[1]: loan_amount(rows, loan_type="home", purpose="medical", days=90),
    }


def summarize(samples):
    ordered = sorted(samples)
    return {"p50_ms": median(ordered), "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1]}


def benchmark(count, repeats, warmups):
    payload = make_payload(count)
    prepared = prepare_rows(payload)
    cases = {
        "raw_aggregation_only": lambda: raw_aggregations(prepared),
        "raw_end_to_end": lambda: raw_aggregations(prepare_rows(payload)),
        "compiled_features": lambda: RUNNER(payload),
        "compute_features": lambda: compute_features(payload, feature_names=FEATURE_NAMES),
    }
    expected = raw_aggregations(prepared)
    for function in cases.values():
        for _ in range(warmups + 1):
            assert function() == expected, "Benchmark paths returned different results"

    samples = {name: [] for name in cases}
    order = list(cases)
    randomizer = random.Random(0)
    for _ in range(repeats):
        randomizer.shuffle(order)  # Avoid always favoring the same execution order.
        for name in order:
            start = perf_counter_ns()
            result = cases[name]()
            elapsed_ms = (perf_counter_ns() - start) / 1_000_000
            assert result == expected, "Benchmark paths returned different results"
            samples[name].append(elapsed_ms)

    return {"rows": count, "timings": {name: summarize(values) for name, values in samples.items()},
            "samples_ms": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=int, default=[1000, 10000, 50000])
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--warmups", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.rows) < 0 or args.repeats < 1 or args.warmups < 0:
        parser.error("rows/warmups must be non-negative and repeats must be positive")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.platform(),
        "gc_enabled": gc.isenabled(), "repeats": args.repeats, "warmups": args.warmups,
        "selected_features": FEATURE_NAMES,
        "runs": [benchmark(count, args.repeats, args.warmups) for count in args.rows],
    }
    print(f"Python {report['python']} | {report['platform']} | {args.repeats} samples per case")
    print(f"{'Rows':>8}  {'Case':<24} {'p50 (ms)':>10} {'p95 (ms)':>10}")
    for run in report["runs"]:
        for name, timing in run["timings"].items():
            print(f"{run['rows']:>8}  {name:<24} {timing['p50_ms']:>10.3f} {timing['p95_ms']:>10.3f}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
