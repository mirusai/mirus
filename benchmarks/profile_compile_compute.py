"""Measure preparation vs execution: python -m benchmarks.profile_compile_compute."""

import argparse
import cProfile
import gc
import io
import json
import math
import platform
import pstats
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from unittest.mock import patch

from mirus.features.decorators import feature, field
from mirus.features.compute import prepare_features, compute_features
from mirus.features import registry

def register_catalog(combinations):
    """Register a synthetic catalog with two windows per loan type."""
    loan_types = ["credit", "home"] + [f"type_{i}" for i in range(combinations // 2 - 2)]

    @field(source="loans")
    def dollars(row) -> float:
        return float(row["amount"]) / 7

    @feature(source="loans", feature_name="loan_{loan_type}_amount_{days}d",
             parameters={"loan_type": loan_types, "days": [30, 90]})
    def amount(rows, *, loan_type, days) -> float:
        return sum((row["dollars"] for row in rows
                    if row["loan_type"] == loan_type and 0 <= row["age_days"] < days), 0.0)


def selected_features(count):
    loan_types = ["credit", "home"] + [f"type_{i}" for i in range(count // 2)]
    return [(f"loan_{loan_type}_amount_{days}d", loan_type, days)
            for loan_type in loan_types for days in (30, 90)][:count]


def make_payload(count, selection):
    loan_types = list(dict.fromkeys(loan_type for _, loan_type, _ in selection))
    randomizer = random.Random(0)
    return {"loans": [
        {"amount": 700 + i % 1000, "loan_type": loan_types[i % len(loan_types)],
         "age_days": randomizer.randrange(120)}
        for i in range(count)
    ]}


def benchmark(rows, combinations, repeats, warmups, selection):
    names = [name for name, _, _ in selection]
    payload = make_payload(rows, selection)
    prepared = prepare_features()
    expected = {
        name: sum((float(row["amount"]) / 7 for row in payload["loans"]
                   if row["loan_type"] == loan_type and 0 <= row["age_days"] < days), 0.0)
        for name, loan_type, days in selection
    }
    cases = {
        "compile_only": lambda: prepare_features(names),
        "execute_only": lambda: compute_features(payload, names, catalog=prepared),
        "combined": lambda: compute_features(payload, feature_names=names),
    }
    assert len(expected) == len(names)
    assert compute_features(payload, names, catalog=prepared) == compute_features(payload, feature_names=names) == expected
    for function in cases.values():
        for _ in range(warmups):
            function()

    order, samples, randomizer = list(cases), {name: [] for name in cases}, random.Random(0)
    result = None
    for _ in range(repeats):
        randomizer.shuffle(order)
        for name in order:
            result = None  # Release the previous sample outside the timed region.
            start = perf_counter_ns()
            result = cases[name]()
            samples[name].append((perf_counter_ns() - start) / 1_000_000)
            if name == "compile_only":
                assert list(result.feature_names) == names
            else:
                assert result == expected

    timings = {}
    for name, values in samples.items():
        timings[name] = {"p50_ms": median(values), "p95_ms": sorted(values)[math.ceil(repeats * .95) - 1]}
    return {"rows": rows, "combinations": combinations, "selected_features": names,
            "nonzero_outputs": sum(value != 0 for value in expected.values()),
            "timings": timings, "samples_ms": samples}


def profile_compilation(iterations, names):
    """Diagnostic profiler only; its instrumented timings are not latency samples."""
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(iterations):
        prepare_features(names)
    profiler.disable()
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats("cumulative").print_stats(12)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=int, default=[100, 1000, 50000])
    parser.add_argument("--combinations", nargs="+", type=int, default=[12, 120, 1200, 12000])
    parser.add_argument("--selected", nargs="+", type=int, default=[2])
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--warmups", type=int, default=10)
    parser.add_argument("--profile-iterations", type=int, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.rows) < 0 or args.repeats < 1 or min(args.warmups, args.profile_iterations) < 0:
        parser.error("rows/warmups/profile-iterations must be non-negative; repeats must be positive")
    if any(count < 4 or count % 2 for count in args.combinations):
        parser.error("combinations must be even and at least four")
    if min(args.selected) < 1 or max(args.selected) > min(args.combinations):
        parser.error("selected counts must be positive and no larger than any catalog")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
        "platform": platform.platform(), "gc_enabled": gc.isenabled(),
        "selected_counts": args.selected, "repeats": args.repeats, "warmups": args.warmups, "runs": [],
        "workload": "Synthetic conditional sums; rows spread across selected loan types, random ages 0-119 days",
    }
    print(f"Python {report['python']} | {report['platform']} | {args.repeats} samples per case", flush=True)
    print(f"{'Combinations':>12} {'Selected':>8} {'Rows':>7} {'Path':<14} {'p50 ms':>10} {'p95 ms':>10}", flush=True)
    for combinations in args.combinations:
        # Benchmark-only isolation: exercise real decorators/APIs without accumulating catalog entries.
        with patch.object(registry, "default_registry", registry.Registry()):
            register_catalog(combinations)
            for selected in args.selected:
                selection = selected_features(selected)
                for rows in args.rows:
                    run = benchmark(rows, combinations, args.repeats, args.warmups, selection)
                    report["runs"].append(run)
                    for name, timing in run["timings"].items():
                        print(f"{combinations:>12} {selected:>8} {rows:>7} {name:<14} {timing['p50_ms']:>10.3f} "
                              f"{timing['p95_ms']:>10.3f}", flush=True)
            if combinations == max(args.combinations) and args.profile_iterations:
                report["compile_profile"] = {
                    "combinations": combinations, "iterations": args.profile_iterations,
                    "selected_count": max(args.selected),
                    "stats": profile_compilation(args.profile_iterations,
                                                 [name for name, _, _ in selected_features(max(args.selected))]),
                }
    if "compile_profile" in report:
        print("Compilation hotspots (instrumented, separate from latency samples):")
        print(report["compile_profile"]["stats"])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
