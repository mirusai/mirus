# How decorators become feature values

mirus separates **defining a feature** from **running it**. You write ordinary
Python functions; the library registers them and coordinates
their execution for each payload.

```text
Definition time:  @field / @feature → registry

Request time:    raw payload → source records + root context → calculated fields
                                                        → selected features → result dict
                └────────────── compute_features() coordinates this ─────────────────────┘
```

The following snippets form one toy example. Run them in order in a fresh Python
process, separately from the other example modules, which register overlapping names.

## 1. Start with a raw payload

This is a Python dictionary with the same structure as a JSON request:

```python
payload = {
    "user_id": "u1",
    "as_of": "2026-10-06",
    "local_per_usd": 7,
    "loans": [
        {"loan_id": "l1", "amount": 700, "created_at": "2026-09-26"},
        {"loan_id": "l2", "amount": 1400, "created_at": "2026-08-01"},
    ],
}
```

The payload can come directly from the caller or from `mirus.serving.OnlineFetcher`.
`compute_features()` itself does not query a database.

## 2. Declare fields and features

```python
from datetime import date

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features


@field(source="loans")
def amount_usd(row) -> float:
    return row["amount"] / row["local_per_usd"]


@field(source="loans")
def age_days(row) -> int:
    return (date.fromisoformat(row["as_of"]) - date.fromisoformat(row["created_at"])).days


@feature(source="loans")
def loan_count(rows) -> int:
    return len(rows)


@feature(
    source="loans",
    feature_name="loan_amount_usd_{days}d",
    parameters={"days": [30, 90]},
)
def loan_amount(rows, *, days) -> float:
    return sum(
        (row["amount_usd"] for row in rows if 0 <= row["age_days"] < days),
        0.0,
    )
```

At definition/import time, the decorators only record the function and its metadata
in the process-local registry. They do not read payloads, prepare rows, or compute
values. The original functions remain directly callable.

- `@field`: the function name becomes a calculated row key, such as `amount_usd`.
- `@feature`: the function receives the prepared rows for its source and returns
  one output value. Without `feature_name`, the output name is the function name.
- `parameters`: one function defines multiple concrete output names. Here,
  `loan_amount_usd_30d` and `loan_amount_usd_90d` bind the same function to different
  `days` arguments. Select these output names, not the function name `loan_amount`.

This toy contract uses ISO date strings. Real fetchers may preserve Python
`datetime` and `Decimal` objects; feature code must use the types agreed in the
project's payload contract. Historical ages must be relative to `as_of`, not today.

The next two sections show intermediate data **inside a compute call**; you do not
need to construct it yourself or call a separate preparation API.

## 3. Raw payload → source rows

For `source="loans"`, the runtime reads `payload["loans"]` and merges root scalar
values into each row. The intermediate rows are:

```json
[
  {
    "user_id": "u1", "as_of": "2026-10-06", "local_per_usd": 7,
    "loan_id": "l1", "amount": 700, "created_at": "2026-09-26"
  },
  {
    "user_id": "u1", "as_of": "2026-10-06", "local_per_usd": 7,
    "loan_id": "l2", "amount": 1400, "created_at": "2026-08-01"
  }
]
```

Every row receives the root context (`user_id`, `as_of`, `local_per_usd`) and the
loan's own fields. One loan always remains one source row. Nested objects and
lists stay in place: access `row["agreement"]["term"]["months"]` explicitly rather
than silently merging child fields or multiplying loans by their payments.

- A source object becomes one row. A source list stays a list. A missing source or an empty list gives the feature no rows, and the feature chooses the result.
- Root dictionaries and lists are not copied into other sources. Scalar values,
  including null, are included.
- Source-row keys override root keys on collisions.
- The runtime makes one shallow row copy. Nested values remain shared and must
  be treated as read-only; user functions must not mutate them.

### Fields can reduce or transform nested lists

This is a separate example, not another declaration to add to the toy functions:

```python
@field(source="loans")
def paid_amount(row) -> float:
    return sum(payment["amount"] for payment in row["payments"])

@field(source="loans")
def payment_amounts(row) -> list[float]:
    return [payment["amount"] for payment in row["payments"]]

@feature(source="loans")
def largest_payment(rows) -> float | None:
    return max(
        (amount for row in rows for amount in row["payment_amounts"]),
        default=None,
    )
```

Fields can return a scalar or a list stored under their function name; neither
changes the source's row count. Handle optional objects explicitly in field
code. Required objects can be accessed directly; retrieval uses empty lists for
missing collections and `None` for missing singular objects.

## 4. Source rows → calculated fields

The runtime calls **every registered field for the selected source once per row**.
Both fields see the same row plus root context; fields cannot depend on another
field's calculated output.

After field evaluation, the shared rows are:

```json
[
  {
    "user_id": "u1", "as_of": "2026-10-06", "local_per_usd": 7,
    "loan_id": "l1", "amount": 700, "created_at": "2026-09-26",
    "amount_usd": 100.0, "age_days": 10
  },
  {
    "user_id": "u1", "as_of": "2026-10-06", "local_per_usd": 7,
    "loan_id": "l2", "amount": 1400, "created_at": "2026-08-01",
    "amount_usd": 200.0, "age_days": 66
  }
]
```

Calculated field keys override existing row keys. All selected features on `loans`
receive this same prepared list; they must treat it as read-only. There is no
field-level dependency analysis.

## 5. Prepared rows → selected features → result

```python
result = compute_features(
    payload,
    feature_names=["loan_count", "loan_amount_usd_30d"],
)
assert result == {"loan_count": 2, "loan_amount_usd_30d": 100.0}

# A later request can choose a different list of features.
assert compute_features(payload, ["loan_amount_usd_90d"]) == {
    "loan_amount_usd_90d": 300.0,
}

# Omitting the list computes all registered features.
assert compute_features(payload) == {
    "loan_count": 2, "loan_amount_usd_30d": 100.0, "loan_amount_usd_90d": 300.0,
}
```

For each call, `compute_features()` prepares the registered definitions,
resolves the two requested names, groups them under `loans`, prepares those source
rows once, and executes the two aggregations. The 90-day aggregation does not run.
Each new request prepares its own definitions and rows; feature values are not cached.

Omitting `feature_names` computes all registered features. An empty list computes
nothing. Unknown names raise `KeyError`. Unused sources are not prepared.
If each selected aggregation scans N rows, K aggregations still perform O(K × N)
work: sharing row preparation does not combine their aggregation scans.

`compute_features()` includes definition preparation on every call. Optional
startup preparation remains available for latency-sensitive services, but is not
required for this usage pattern; see the main README's optional startup preparation section.

Project-definition checks run separately in development/CI, using the optional
`mirus-validation` package. Import your feature modules and call
`validate_project("payload.yaml")` with the matching project contract. This checks
all declarations without computing any values. Preparation does not repeat those
checks on each request; validation does not configure the serving process.

## 6. How backtesting reuses the same logic

There is no second set of feature formulas for Spark. The intended distinction is
**how the payload is retrieved and how many payloads are processed**, not how a
feature is defined.

### Available today: common callables and output metadata

With the backtest distribution installed, the same declarations produce offline
metadata. Continue in the same process after defining the toy functions above:

```python
from mirus.backtest import compile_offline

names = ["loan_count", "loan_amount_usd_30d"]
offline_plan = compile_offline(names)

assert offline_plan.output_schema == {
    "loan_count": int,
    "loan_amount_usd_30d": float,
}
assert offline_plan.required_sources == ("loans",)
assert offline_plan.fields_by_source["loans"]["amount_usd"] is amount_usd
assert offline_plan.features_by_source["loans"]["loan_count"] is loan_count
amount_30d = offline_plan.features_by_source["loans"]["loan_amount_usd_30d"]
assert amount_30d.func is loan_amount
assert amount_30d.keywords == {"days": 30}
```

The last callable is a `functools.partial` of the original function—not a rewritten
SQL implementation. `OfflinePlan.output_types` derives return annotations from its
selected catalog when read; the compiler does not store a separate type list.
Annotations do not validate or coerce runtime values. `OfflinePlan` is metadata,
not an executable Spark job or a payload accepted by `compute_features()`.

### Available today: historical payload retrieval

`SparkFetcher` reads warehouse tables, applies
point-in-time joins, and assembles one payload column per driver observation using
the shared YAML contract. A driver row identifies an entity and its observation
time; it is not necessarily one row per unique user.

The following sketch requires your Spark session, driver DataFrame, and configured
warehouse tables. It uses the existing retrieval example, not the toy payload above:

```python
from mirus.backtest.spark.fetcher import SparkFetcher
from mirus.payload import Payload

definition = Payload.from_yaml("examples/credit_application.yaml")
historical = SparkFetcher(definition, spark).fetch(driver)
# historical contains observation keys/time plus a nested Spark struct: payload.
```

The shared contract maps online DB and warehouse fields into consistent payload
names. Historical correctness additionally requires appropriate availability
timestamps and complete version history; identical feature functions alone cannot prevent
future-data leakage. The toy date strings, currency context, and source fields
would need a matching contract in a real project.

Set `mutable: true` on a source whose values can change. Its `dwh` table must contain
complete historical row snapshots, including the initial and latest versions.
No separate mutation-table key, CDC metadata columns, or union with today's state are required.
For example, keep the original creation date as a business field and mark the
version's availability time separately:

```yaml
mutable: true
db: lending.loan_record
dwh: lending_history.loan_record_versions
fields:
  loan_id: {type: string, primary_key: true}
  createdat: {type: timestamp}
  updatedat: {type: timestamp, available_at: true}
```

This is a fragment of a payload section; keep its relationship and other fields.
PIT retrieval selects the latest version available for each observation, preserving
all distinct loan IDs. Key/time pairs must be unique, and hard deletes are not yet
supported. Use `mutable: false` (the default) for immutable warehouse records; this
skips the version window. Each nested source declares its own mutability.

### Distributed feature execution

```text
Online:   request / OnlineFetcher → Python payload → compute_features → result dict
Offline:  driver + warehouse → SparkFetcher PIT payloads
                           → Arrow UDF / pandas UDF → same compute_features → feature columns
```

```python
from mirus.backtest import Backtest
from mirus.backtest.spark.config import RECOMMENDED_SPARK_CONFIG

for key, value in RECOMMENDED_SPARK_CONFIG.items():
    spark.conf.set(key, value)
# Override recommendations directly when needed.
spark.conf.set("spark.sql.execution.arrow.maxRecordsPerBatch", "128")

driver = spark.table("analytics.credit_observations")
backtest = Backtest(
    payload_yaml="examples/credit_application.yaml",
    backend="spark.pandas",  # Or "spark"; definitions and results remain the same.
)
features = backtest.compute(
    driver,
    feature_names=["loan_count", "loan_total_amount_usd"],
)
```

The recommendations are optional, not automatically applied. Users create and
configure Spark themselves; the driver DataFrame supplies its session to backtesting.
Arrow batch size counts observations, not the records nested inside each payload.

This sketch uses the `loan_features` definitions and warehouse contract, not the
earlier toy date-string payload. Import that module first. `Backtest` parses the YAML
once when created. Each compute call prepares a fresh selected catalog and retrieves
only its required top-level sources, retaining
their full nested data. It maps scalar return annotations into a flat Spark schema.
Both paths return observation keys/time plus selected feature columns, without
triggering an action; write or otherwise consume the result to execute the job.

Omit `feature_names` to compute all imported features, or pass `[]` to return only
observation keys/time without source joins or a UDF. Reuse the same `Backtest`
instance for different selections; the original payload contract is not modified.

The Arrow scalar UDF uses `asDict(recursive=True)` to convert nested Rows. The
pandas UDF receives dictionaries and arrays and normalizes containers and timestamps
without input-schema conversion. IDs must arrive as strings before entering Spark;
exact integer inputs and results must stay within `-2**53` to `2**53`. Larger
integers are outside the offline precision contract; no runtime range checking
or automatic ID conversion is added.
The pandas path loops through observations; users do not rewrite features as
pandas operations. Customer feature modules and dependencies must be installed on
workers. Matching logic also requires matching input types, UTC reference times and
null semantics across online and offline payloads.

## Runnable examples and fixtures

Run each demo separately from the repository root:

| Command | Demonstrates |
| --- | --- |
| `python -m examples.loan_features` | Single-output features and direct computation |
| `python -m examples.parameterized_features` | Feature families and selecting concrete output names |
| `python -m examples.serve_features` | A serving function with different selections per request |

The feature definition demos register overlapping names, so do not import both
into the same application. The serving demo imports only `loan_features`.

`credit_application.yaml` defines the shared retrieval payload contract.
`credit_application.payload.json` shows the expected payload used by retrieval tests.

Latency tools live in `benchmarks/`, not in the application usage examples.
