"""Per-request feature selection: python -m examples.serve_features."""

from mirus.features.compute import compute_features

# This import registers user-written functions; mirus does not scan files.
from examples import loan_features  # noqa: F401


FEATURE_NAMES = ["loan_count", "loan_total_amount_usd"]


def score_payload(payload, feature_names=None):
    names = FEATURE_NAMES if feature_names is None else feature_names
    return compute_features(payload, feature_names=names)


if __name__ == "__main__":
    print("Request 1:", score_payload({"loans": [{"principal": 700}, {"principal": 1400}]}))
    print("Request 2:", score_payload({"loans": [{"principal": 350}]}, ["loan_count"]))
