# Focused test suites

Run from the repository root after installing mirus and the local backtest package
(`python -m pip install -e '.[test]' -e ./mirus/backtest`).
No live database or Spark session is needed for unit tests.

| Command (`python -m pytest … -q`) | Focus |
| --- | --- |
| `tests/features/test_preparation.py` | Catalog snapshots, request subsets, selected binding, serialization |
| `tests/features/test_execution.py` | Shared rows, root context, field execution, payload preservation |
| `tests/features/test_parameterized.py` | Parameter combinations, names, declaration validation |
| `tests/features/test_compute.py` | Public API and registration lifecycle |
| `tests/retrieval` | Payload contracts, mocked MySQL reads, online interface |
| `tests/offline` | Offline interface and feature metadata parity |
| `tests/packaging` | Fresh-process imports; wheel isolation when `MIRUS_WHEEL_DIR` is set |
| `tests/benchmarks` | Benchmark correctness, never machine-dependent latency thresholds |
| `tests/integration` | Opt-in live MySQL and local Spark checks |

Default suite: `python -m pytest -q`.

Integration tests require `MYSQL_LATENCY_TEST=1` or `SPARK_INTEGRATION_TEST=1`.
Do not run other tests concurrently with latency measurements.

Build and verify the separate distributions without downloading dependencies:

```sh
python -m pip wheel --no-index --no-deps --no-build-isolation \
  --wheel-dir .cache/refactor-wheels . ./mirus/backtest
MIRUS_WHEEL_DIR="$PWD/.cache/refactor-wheels" python -m pytest tests/packaging -q
```

Use a clean build directory after moving package files; setuptools can retain
obsolete files in incremental build output. Wheel tests detect those leftovers.
