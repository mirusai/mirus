"""Smoke-test the benchmark without asserting machine-dependent latency budgets."""

import json
from pathlib import Path
import subprocess
import sys


def test_feature_benchmark_reports_comparable_paths(tmp_path):
    output = tmp_path / "benchmark.json"
    subprocess.run(
        [sys.executable, "-m", "benchmarks.benchmark_features", "--rows", "0", "20",
         "--repeats", "3", "--warmups", "0", "--output", str(output)],
        cwd=Path(__file__).resolve().parents[2], check=True, capture_output=True, text=True,
    )
    report = json.loads(output.read_text())
    assert [run["rows"] for run in report["runs"]] == [0, 20]
    for run in report["runs"]:
        assert set(run["timings"]) == {
            "raw_aggregation_only",
            "raw_end_to_end",
            "compiled_features",
            "compute_features",
        }
        for name, timings in run["timings"].items():
            assert len(run["samples_ms"][name]) == 3
            assert 0 <= timings["p50_ms"] <= timings["p95_ms"]


def test_compile_compute_profile_reports_separate_timings(tmp_path):
    output = tmp_path / "profile.json"
    subprocess.run(
        [sys.executable, "-m", "benchmarks.profile_compile_compute", "--rows", "0", "20",
         "--combinations", "4", "12", "--selected", "2", "4", "--repeats", "3", "--warmups", "0",
         "--profile-iterations", "1", "--output", str(output)],
        cwd=Path(__file__).resolve().parents[2], check=True, capture_output=True, text=True,
    )
    report = json.loads(output.read_text())
    assert len(report["runs"]) == 8
    assert report["selected_counts"] == [2, 4]
    assert report["compile_profile"]["combinations"] == 12
    for run in report["runs"]:
        assert len(run["selected_features"]) in (2, 4)
        if run["rows"] == 0:
            assert run["nonzero_outputs"] == 0
        else:
            assert 0 < run["nonzero_outputs"] <= len(run["selected_features"])
        assert set(run["timings"]) == {"compile_only", "execute_only", "combined"}
        for name, timing in run["timings"].items():
            assert len(run["samples_ms"][name]) == 3
            assert 0 <= timing["p50_ms"] <= timing["p95_ms"]
