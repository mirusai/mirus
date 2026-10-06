# mirusai

Licensed under the [MIT License](LICENSE).

**mirus** is the Python library for reusable feature computation and payload retrieval.
The optional **mirus-backtest** distribution adds offline retrieval and metadata support.

This is a small, **design-stage implementation**, not a production release.
The main data paths have a mocked online demonstration and an opt-in local Spark
integration test. Comprehensive validation, production database coverage,
policy configuration and performance tuning remain deferred.

## Standalone feature functions

Users write ordinary functions, not classes. `@field(source="loans")` declares
a per-row transformation; `@feature(source="loans")` declares an aggregation.
See `examples/loan_features.py` for a complete runnable example.

`@feature` has only two forms: a single named output, or a parameterized family
of named outputs. Both use the function's return annotation for schema inspection.
Dictionary groups and the `output_types` decorator argument are no longer supported.

```python
from mirus.features.compute import compute_features
from examples import loan_features  # Import executes the decorators.

payload = {"loans": [{"principal": 700}, {"principal": 1400}]}

result = compute_features(payload, feature_names=["loan_count"])
# {"loan_count": 2}

result = compute_features(payload, ["loan_count", "loan_total_amount_usd"])
# {"loan_count": 2, "loan_total_amount_usd": 300.0}
```

Customer functions can live anywhere. Import definition modules before computing;
decorators register declarations in the current process. mirus does not scan files
or import arbitrary modules. The default call handles preparation and computation.

### Package boundaries

```text
mirusai/
├── mirus/
│   ├── __init__.py        # Package metadata only
│   ├── features/
│   │   ├── decorators.py # Register @field and @feature declarations
│   │   ├── definitions.py
│   │   ├── registry.py
│   │   ├── compiler.py   # prepare_features()
│   │   └── compute.py    # Shared pure-Python feature computation
│   ├── payload/         # Shared YAML/payload contract
│   ├── serving/
│   │   ├── interface.py # OnlineFetcher and OnlineBackend protocol
│   │   └── mysql.py     # MySQLFetcher and connection helper
│   └── backtest/        # Separate mirus-backtest distribution
│       ├── interface.py # OfflineFetcher and OfflineBackend protocol
│       ├── compiler.py  # compile_offline()
│       ├── plan.py
│       └── spark/
│           ├── fetcher.py
│           └── temporal.py
├── examples/             # Current usage and payload examples
├── benchmarks/           # Development-only latency measurements
└── tests/
```

Other-language data pullers can live outside `mirus/`, for example under a future
`extensions/` directory. They are not included in Python distributions.


Online and offline use separate backend protocols and share the payload contract.
Feature execution is independent of retrieval. Spark UDF/Pandas UDF execution and
Spark output-schema conversion are future work, not part of this refactor.

Public imports are grouped by responsibility:

| Responsibility | Import from |
| --- | --- |
| `feature`, `field` | `mirus.features.decorators` |
| `compute_features` | `mirus.features.compute` |
| Optional `prepare_features` | `mirus.features.compute` |
| `Payload` and shared contract types | `mirus.payload` |
| `OnlineFetcher`, `MySQLConnection` | `mirus.serving` |
| `OfflineFetcher`, `compile_offline` | `mirus.backtest` |

The mixed exports from `mirus` and `mirus.features` have been removed; update
existing imports to these modules. Computation does not import backtest code.

### Optional parameterized features

One function can define multiple independently selectable features:

```python
from mirus.features.decorators import feature
from mirus.features.compute import compute_features

@feature(
    source="loans",
    feature_name="loan_{purpose}_amount",
    parameters={"purpose": ["shopping", "medical"]},
)
def loan_amount(rows, *, purpose) -> float:
    return sum((row["amount"] for row in rows if row["purpose"] == purpose), 0.0)

result = compute_features(
    {"loans": [{"amount": 700, "purpose": "shopping"}]},
    feature_names=["loan_shopping_amount"],
)
# {"loan_shopping_amount": 700.0}; the medical aggregation is never called.
```

- `parameters` is optional; omitted or empty parameters preserve existing behavior.
- Choices are non-empty sequences. Multiple parameters expand to their Cartesian
  product during preparation. All names are validated, but only selected callables are bound; no feature values are computed.
- Select the **concrete output name**, not the underlying Python function name.
  The function's return annotation becomes the type of each concrete output.
- Name templates must produce unique names. Invalid declarations remain raw and
  are rejected before either compiler returns a partial plan.
- Each parameter combination computes one named value, just like a single feature.
- The framework passes parameter values unchanged. Window semantics, including
  the difference between 90 days and three calendar months, belong to user logic.

Run `python -m examples.parameterized_features` for loan-type, purpose and time-window
crosses. Preparation is shared per source; only selected combinations execute.
Preparing C combinations across P parameters checks O(C × P) name/argument work; only selected callables are retained.
If each selected aggregation scans N rows, K selections still require O(K × N)
aggregation work. There is no automatic shared-scan optimization or plan caching.

### Execution contract

- Prepare **all fields for each selected source once**, then execute **only the
  selected aggregation functions**. Do not read or prepare unselected sources.
- `source` currently names one top-level payload section. Lists stay as rows;
  a single object becomes one row and `None` becomes zero rows. Each segment row
  receives the root context scalars. Nested objects are inlined onto that row
  from the deepest object; a key already on the nearer row overrides the same
  key from deeper down. A list of records nested under the row repeats the row
  once per element, so two payments produce two loan rows. Lists that are not
  nested under that row are not joined in. Multiple-source functions and nested source paths remain
  deferred. Retrieval still returns the nested payload; MySQL and Spark do not
  flatten it.
- Root-level scalar values (including null) are added to each source row before
  field transformations; root objects and lists are excluded. Source-row values
  override root values, and calculated fields override both. Empty sources stay empty.
- Field functions read that flattened row and are independent of other fields.
  Their results are added onto it. Aggregations share those prepared
  rows. User functions must be pure/read-only.
- Single outputs use the function name or `feature_name`. Parameterized outputs
  use the concrete name generated from the template. Each selected callable's
  return value is stored under exactly one key; there is no dictionary flattening.
  Online computation can use unannotated functions; schema inspection requires a
  return annotation.
- Omitted `feature_names` (or `None`) selects everything; `[]` executes nothing.
  Repeated names are deduplicated. Unknown names fail before any computation;
  duplicate declarations fail during compilation. Use a list, not a bare string.
- `compile_offline().output_schema` records Python types (including nullable unions) without
  executing user functions. It does not
  coerce/validate runtime values. Spark schema conversion and UDF wrappers are
  not implemented in this first structural pass.

### Optional startup preparation (advanced)

Start with `compute_features(payload, feature_names=...)`. Preparation is an
optimization, not a required setup step; use it when measurements justify it.

```python
from mirus.features.compute import compute_features, prepare_features
from examples import loan_features

catalog = prepare_features(["loan_count", "loan_total_amount_usd"])
result = compute_features(
    {"loans": [{"principal": 700}]},
    feature_names=["loan_count"],
    catalog=catalog,
)
```

Without a catalog, `compute_features()` prepares a fresh selection on each call.
With a catalog, it performs O(K) name lookup/grouping for K requested features,
prepares each selected source once, and runs selected aggregations. It does not
access the registry, infer schemas, or expand parameter combinations.
An unavailable name raises `KeyError`; the catalog is never silently expanded.

Preparation still validates all registered names, even when selecting a subset.
Restricting preparation avoids binding unneeded callables, but does not eliminate
the scan over parameter combinations needed to detect duplicate names.
No LRU or global prepared-plan cache is used.

Catalogs are snapshots of declarations. Treat their mappings and shared prepared
rows as read-only. Field functions receive root context plus the flattened
source row, not outputs of other fields. Register definitions before serving, and rebuild the
catalog when definitions change.

Importable module-level callables can be serialized with the catalog. Workers
using a supplied catalog do not read the registry; closures may require the
execution engine's serializer.

Run `python -m examples.serve_features` for direct computation with per-request
selection. See [tests/README.md](tests/README.md) for focused suites.

## Three parts

1. **YAML -> Payload object.** `Payload.from_yaml()` loads each section into a
   `PayloadSection` dataclass. One top-level `Payload` owns the tree, metadata and
   `validate()` method. Each section carries fields, tables, a parent relationship,
   optional DB-to-DWH column mappings, and named children. There is no separate
   loader function or `Dataset` class to work with.
2. **OnlineFetcher facade.** The stable public interface returns a dictionary
   while delegating to the current MySQL implementation. MySQL translates the
   object into parameterized reads, fetching all related keys for a section
   together and attaching nested records.
3. **OfflineFetcher facade.** The stable public interface returns the engine's
   native data frame while delegating to the current Spark implementation. Spark
   maps warehouse columns, joins at each observation's time, and assembles
   children bottom-up into a nested `payload` column.

The two fetcher interfaces are independent. Their shared payload description lives
under `mirus/payload/`, imported as `mirus.payload`; it belongs to retrieval,
not feature computation. Backend imports are lazy, so importing either facade does not load
PyMySQL or Spark. Callers do not construct backend fetcher classes directly.
`OnlineFetcher` defaults to the `mysql` backend and `OfflineFetcher` defaults to
`spark`; both facades contain an explicit extension point for future backends.

## Read the implementation in this order

- `mirus/payload/model.py`: `Payload`, `PayloadSection`, `Field`, `Relationship` and
  `JoinKey`, including YAML loading and basic definition validation.
- `mirus/serving/interface.py`: `OnlineFetcher`.
- `mirus/serving/mysql.py`: online reads and dictionary assembly.
- `mirus/backtest/interface.py`: `OfflineFetcher` without engine dependencies.
- `mirus/backtest/spark/temporal.py`: historical rows and availability intervals.
- `mirus/backtest/spark/fetcher.py`: PIT joins and nested grouping.

The source folders now match their Python imports directly: `mirus/serving/`
is `mirus.serving`, and `mirus/payload/` is `mirus.payload`.
The serving distribution name is `mirus`; the project is **mirusai**.

## Usage sketches

Load the agreed payload-shaped YAML:

```python
from mirus.payload import Payload

payload = Payload.from_yaml("examples/credit_application.yaml")
payload.validate()

loans = payload.root.children["loans"]
agreement = loans.children["agreement"]
```

`validate()` checks definition-level keys, time markers, relationships and field
references. It returns the same `Payload`, so loading and validation can also be
chained. Neither step reads customer data. Backend data validation remains deferred.

### Internal typed tree (AST)

Users still author only YAML. Loading translates `type` into `Field.data_type`,
`relationship.on` into a list of `JoinKey(parent, child)` objects, and the optional
`field_mapping` into each field's `dwh_column`. An omitted mapping defaults to
the field's DB/payload name. Relationships use `Relationship.cardinality`.

This existing section tree is the AST; there is no second tree or generic compiler
framework. MySQL and Spark consume typed attributes, not YAML dictionaries.
For example, `loans.fields["principal"].dwh_column` is `"loan_amount"` and
`loans.relationship.keys` contains the composite parent/child join keys.
Malformed references in `field_mapping` are rejected during translation, before
the mapping is folded into fields. `validate()` checks the resulting tree.

Online, build and warm a serving connection before constructing the fetcher:

```python
from mirus.serving import MySQLConnection, OnlineFetcher

connection = MySQLConnection(
    host="127.0.0.1",
    user="mirus",
    password="secret",
    database="lending",
    autocommit=False,
).connect()
fetcher = OnlineFetcher(payload, connection=connection)
data = fetcher.fetch({
    "observation_id": "application-123",
    "tenant_id": "tenant-1",
    "user_id": "user-42",
})
# data contains loans, devices, login_behavior and third_party_data collections.
# Each loan also contains an agreement object (or None).
```

Offline, using an existing Spark session and an observation driver:

```python
from mirus.backtest import OfflineFetcher

fetcher = OfflineFetcher(payload, session=spark)
driver = spark.table("training.applications").select(
    "observation_id", "tenant_id", "user_id", "as_of"
)
data = fetcher.fetch(driver)
# Columns: observation_id, as_of, payload (a nested Spark struct).
# A later feature UDF can consume data.payload. No UDF is used for retrieval.
```

The root primary-key fields identify an observation, not just a customer. Every
nested join inherits that observation's `as_of`. Child identities also include
their ancestry, so repeated entities in different observations stay separate.

### Multiple root sections

The example includes four sibling sections: `loans`, `devices`, `login_behavior`
and `third_party_data`. Each joins directly to the request's tenant/user keys.
Both fetchers iterate every child of a section, not just the first one. Spark
aggregates each sibling separately before attaching it, so multiple devices do
not multiply the loan list. The same recursion handles nested children such as
`loans.agreement`. Third-party data in this example is already stored in the DB
and warehouse; live external API fetching is not implemented.

## Historical input convention

- **No mutation table:** the warehouse table contains immutable records. A row
  is available from its marked timestamp onward.
- **With a mutation table:** the base contains each record's **initial version**,
  not its overwritten current state. Changes carry full after-images and three
  fixed metadata columns: `_available_at` (timestamp), `_sequence` (positive long,
  unique for a record at a timestamp), and `_operation` (`UPSERT` or `DELETE`).
- Deletes retain primary keys and metadata; their other business fields may be
  null. The table still exposes all mapped business columns.
- Initial rows use sequence zero. Changes are ordered by availability and
  sequence. Each version is valid on `[available_at, next_available_at)`.
- Intervals are calculated **before** dropping deletes and **before** joining to
  parents. This prevents resurrecting deleted rows or retaining old ownership.
- The same `field_mapping` applies to base and mutation business columns. Mutation
  metadata is separate: the original `createdat` is not an update timestamp.

Snapshot ingestion, partial update images, CDC normalization and completeness
watermarks are outside this first pass. An extra table name alone does not make
current-state data historically correct.

## Current assumptions / deferred work

- YAML is trusted and follows the example; `Payload.validate()` provides basic
  structural checks, not complete schema or data validation. Field types are
  recorded but values are not coerced or validated. Fields are scalar and their
  names are simple identifiers, without
  dots, metadata-name collisions or the reserved `__mirus_` prefix.
- Source business column types already agree with the contract and each other.
- Root observation keys and source primary keys are non-null and unique at a
  given time. One-to-one/many-to-one joins match at most one row; cardinality
  validation is deferred. Invalid input may produce incorrect results.
- A missing object becomes `None`/null, a missing collection becomes `[]`.
  Collections are ordered by their declared primary keys.
- MySQL uses a dedicated caller-owned connection with no active transaction and
  compatible case-sensitive key semantics. `MySQLConnection.connect()` performs
  one-time warm-up and configures UTC and repeatable-read session settings.
  Requests are serial per connection. The fetcher opens a read-only snapshot and
  rolls back at the end; the caller closes or returns the connection. Pool
  integration, timeouts, pagination and collection limits are deferred.
- Online `as_of` is generated in UTC (naive datetime) and replaces any supplied
  value; this is current retrieval, not historical MySQL lookup. The database
  snapshot is not exactly identified by that wall-clock timestamp. Exact serving
  replay would need recorded payloads or source version information.
- Spark should use a UTC session and compatible timestamp columns. Inputs must
  be stable/version-pinned during execution. The returned DataFrame is lazy;
  `fetch()` does not run distributed checks or collect data to the driver.
- Shared value normalization, historical completeness checks, validation,
  connection lifecycle extensions and query optimization can be added after
  these module boundaries are agreed.

## Dependencies and verification status

`pyproject.toml` builds the serving distribution (`mirus`), with PyYAML
and an optional MySQL dependency. It excludes all offline source files.
`mirus/backtest/pyproject.toml` builds `mirus-backtest`; its `spark` extra installs
PySpark. The root `spark` extra references this offline distribution for published
installs. Local development should install both paths explicitly.

| Install | Included components |
| --- | --- |
| `mirus` | Shared payload contracts, feature computation, Python serving code |
| `mirus[mysql]` | Above plus the PyMySQL dependency |
| `mirus-backtest[spark]` | Backtest code, shared mirus dependency, and PySpark |
| `mirus[spark]` | Convenience extra selecting the same Spark backtest add-on |

Folder placement alone does not define installation contents: each distribution
explicitly lists its packages. The serving wheel excludes `mirus/backtest/`.

### Payload demonstration test

After installing mirus, run:

```sh
python -m pip install -e '.[test]' -e ./mirus/backtest
python -m pytest -q
```

`tests/retrieval/test_payload_mysql.py` loads the actual example YAML, validates its section
tree, and exercises `OnlineFetcher` with a mocked DB-API connection.
It compares the complete returned payload with
`examples/credit_application.payload.json` and prints the result. Additional tests
cover the typed AST, column mappings and validation of typed join/time references.
All unit tests pass.
The fixture has two loans with nested agreements, two devices, two login events,
and one stored third-party report. Dates and decimals are serialized only for
JSON display; the runtime result preserves Python datetime/Decimal objects.

The test caught and fixed PyYAML's default interpretation of the key `on` as a
boolean. Payload loading now uses a local safe loader with true/false-only
boolean resolution, preserving the agreed relationship syntax.

The mock supplies query result rows: it does not execute MySQL SQL or verify
database filtering or MySQL PIT accuracy.

### Spark payload integration test

`tests/integration/test_spark.py` runs `OfflineFetcher` on a local Spark session with
temporary warehouse views. It verifies that the resulting `payload` cell contains
the root observation, ordered sibling collections, and the nested loan agreement.
It is skipped during normal unit-test runs.

After installing Java 17 and the Spark extra, run:

```sh
python -m pip install -e '.[test]' -e './mirus/backtest[spark]'
SPARK_INTEGRATION_TEST=1 pytest tests/integration/test_spark.py -v
```

### MySQL serving latency test

`tests/integration/test_mysql_latency.py` is an opt-in integration test that measures the full
`OnlineFetcher.fetch()` path against a real MySQL server. It creates and populates
a connection-local temporary table, warms the connection, and checks p95 and p99
latency. It is skipped during normal unit-test runs.

Install the MySQL extra and run it against a non-production database:

```sh
python -m pip install -e '.[mysql]'
MYSQL_LATENCY_TEST=1 \
MYSQL_DATABASE=mirus_test \
MYSQL_USER=root \
MYSQL_PASSWORD=secret \
pytest tests/integration/test_mysql_latency.py -v
```

`MYSQL_HOST` and `MYSQL_PORT` default to `127.0.0.1` and `3306`. The default run
uses 20 warmups and 200 measured requests with budgets of 20 ms at p95 and 35 ms
at p99. Override them with `MYSQL_LATENCY_WARMUPS`,
`MYSQL_LATENCY_ITERATIONS`, `MYSQL_LATENCY_P95_MS`, and
`MYSQL_LATENCY_P99_MS` to match the serving SLO and test environment. Run the
test from the same network location as the serving process for meaningful
results.

The earlier, more elaborate draft is preserved in `.drafts/previous-design/`.
It is not imported or included in the package.

### Raw function vs framework latency benchmark

```sh
.venv/bin/python -m benchmarks.benchmark_features \
  --rows 1000 10000 50000 --repeats 100 --warmups 10 \
  --output .cache/feature_latency.json
```

This compares two selected parameter combinations through three paths:

- **Raw aggregation only:** directly call the original decorated function twice,
  using already prepared rows. Field preparation is excluded.
- **Raw end-to-end:** shallow-copy rows, call the same field function, then call
  the same two aggregations directly. This is the comparable manual baseline.
- **compute_features:** include fresh plan preparation, all fields for the source,
  the two selected aggregations and result assembly. No plan caching is used.

The benchmark checks identical results, warms each path, shuffles measurement
order and reports p50/p95 plus raw samples and environment metadata in JSON.
Payload generation, imports and correctness assertions are outside timed regions.
GC is left at its normal setting. This is a sequential, in-memory microbenchmark:
it excludes DB retrieval, JSON parsing, HTTP, concurrency and network latency.
The raw end-to-end baseline uses hand-written preparation for this one field;
the framework has additional generic dispatch and preparation overhead.
Results are machine/workload specific, not a production latency guarantee.

### Compilation vs execution profiling

```sh
.venv/bin/python -m benchmarks.profile_compile_compute \
  --rows 100 1000 50000 --combinations 12 120 1200 12000 \
  --repeats 100 --warmups 10 --output .cache/compile_compute_profile.json
```

This measures restricted `prepare_features(names)`, request execution against a
full prepared catalog, and `compute_features(payload, names)` without a catalog.
Use `--selected 300 500 --combinations 12000` for model-sized selections from a
12k pool. Execution includes per-request selection, shared field preparation,
and selected aggregations. Payload records are spread over the selected loan
types, with seeded random ages; this is a synthetic conditional-sum workload.

Registration, payload generation, correctness checks and profiler instrumentation
are outside latency measurements. Each case has randomized ordering, warmups,
100 samples by default, p50/p95 and saved raw samples. Garbage collection remains
enabled. A separate cProfile pass identifies compilation hotspots for the largest
catalog; those instrumented times must not be treated as request latency.
This is single-process, in-memory execution, excluding imports, DB fetching,
network, JSON parsing and concurrent serving. No production code is changed by
the benchmark; its private registry replacement is confined to the benchmark run.
