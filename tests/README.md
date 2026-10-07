# Focused test suites

Run from the repository root after installing mirus and the local backtest package
(`python -m pip install -e '.[test]' -e ./mirus/backtest -e ./mirus/validation`).
No live database or Spark session is needed for unit tests.

The online feature suite only needs `mirus` and pytest:

```sh
python -m pip install -e '.[test]'
python -m pytest tests/features -q
```

| Command (`python -m pytest … -q`) | Focus |
| --- | --- |
| `tests/features/test_preparation.py` | Catalog snapshots, catalog precedence, selected binding, serialization |
| `tests/features/test_execution.py` | Source grain, nested lists, shared rows, independent fields, result order |
| `tests/features/test_parameterized.py` | Parameter combinations and selected execution |
| `tests/features/test_compute.py` | Public API and registration lifecycle |
| `tests/validation` | Payload structure, duplicate declarations, signatures, names, annotations, source compatibility |
| `tests/retrieval` | Mocked MySQL reads, empty data, cursors, transaction cleanup |
| `tests/offline` | Offline interface, output annotations and feature metadata parity |
| `tests/offline/test_backtest_interface.py` | Backend selection, single YAML load, fresh selections and source pruning; no Spark imports |
| `tests/offline/test_spark_udf.py` | Scalar schema mapping, array normalization and Spark engine delegation; optional Spark/pandas imports, no session |
| `tests/packaging` | Fresh-process imports; wheel isolation and the full feature suite against the core-only wheel when `MIRUS_WHEEL_DIR` is set |
| `tests/integration` | Opt-in live MySQL; Spark observation isolation, complete version history, availability boundaries, mutable relationships, retrieval/compute parity |

Default suite: `python -m pytest -q`.

Tests cover the production packages and installation boundaries. Payload fixtures
live in `tests/fixtures`; examples and benchmark scripts are not tested.

Integration tests require `MYSQL_LATENCY_TEST=1` or `SPARK_INTEGRATION_TEST=1`.
Do not run other tests concurrently with latency measurements.

Spark needs Java and matching driver/worker Python versions. For example:

```sh
SPARK_INTEGRATION_TEST=1 PYSPARK_PYTHON="$PWD/.venv/bin/python" \
  .venv/bin/python -m pytest tests/integration/test_pit.py tests/integration/test_spark.py tests/integration/test_spark_compute.py -q
```

Build and verify the separate distributions without downloading dependencies:

```sh
python -m pip wheel --no-index --no-deps --no-build-isolation \
  --wheel-dir .cache/refactor-wheels . ./mirus/backtest ./mirus/validation
MIRUS_WHEEL_DIR="$PWD/.cache/refactor-wheels" python -m pytest tests/packaging -q
```

Use clean build directories after moving package files, including the root
`build/`, `mirus/backtest/build/` and `mirus/validation/build/`; setuptools can retain obsolete files or
cached source in incremental output. Wheel tests compare file names and contents
against the current source tree.
