"""Runnable standalone definitions: python -m examples.loan_features."""

from mirus import compute_features, feature, field


@field(source="loans")
def dollar_amount(row) -> float:
    return float(row["principal"]) / 7  # Illustrative conversion rate.


@feature(source="loans")
def loan_count(rows) -> int:
    return len(rows)


@feature(source="loans", feature_name="loan_total_amount_usd")
def total_amount(rows) -> float:
    return sum((row["dollar_amount"] for row in rows), 0.0)


@feature(source="loans")
def largest_loan_amount_usd(rows) -> float | None:
    return max((row["dollar_amount"] for row in rows), default=None)


if __name__ == "__main__":
    payload = {"loans": [{"principal": 700}, {"principal": 1400}]}
    print("All:", compute_features(payload))
    print("Selected:", compute_features(payload, feature_names=["loan_count", "loan_total_amount_usd"]))
