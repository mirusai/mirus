# Current usage examples

Run each demo separately from the repository root:

| Command | Demonstrates |
| --- | --- |
| `python -m examples.loan_features` | Single-output features and direct computation |
| `python -m examples.parameterized_features` | Feature families and selecting concrete output names |
| `python -m examples.serve_features` | Optional startup preparation and different selections per request |

The feature definition demos register overlapping names, so do not import both
into the same application. The serving demo imports only `loan_features`.

`credit_application.yaml` defines the shared retrieval payload contract.
`credit_application.payload.json` shows the expected payload used by retrieval tests.

Latency tools live in `benchmarks/`, not in the application usage examples.
