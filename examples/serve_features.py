"""Optional startup preparation: python -m examples.serve_features."""

from mirus.features import compute_features, prepare_features

# This import runs the decorators. mirus does not scan the customer's files.
from examples import loan_features  # noqa: F401


FEATURE_NAMES = ["loan_count", "loan_total_amount_usd"]
CATALOG = prepare_features(FEATURE_NAMES)


def score_payload(payload, feature_names=None):
    # Each request can choose a different subset of the prepared catalog.
    return compute_features(payload, feature_names, catalog=CATALOG)


if __name__ == "__main__":
    print("Available features:", CATALOG.feature_names)
    print("Request 1:", score_payload({"loans": [{"principal": 700}, {"principal": 1400}]}))
    print("Request 2:", score_payload({"loans": [{"principal": 350}]}, ["loan_count"]))
