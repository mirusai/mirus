"""One function, many selectable features: python -m examples.parameterized_features."""

from mirus.features.decorators import feature, field
from mirus.features.compute import compute_features


@field(source="loans")
def dollar_amount(row) -> float:
    return float(row["amount"]) / 7  # Illustrative conversion rate.


@feature(source="loans")
def loan_count(rows) -> int:
    return len(rows)


@feature(
    source="loans",
    feature_name="loan_{loan_type}_{purpose}_amount_{days}d",
    parameters={
        "loan_type": ["credit", "home", "private"],
        "purpose": ["shopping", "medical"],
        "days": [30, 90],
    },
)
def loan_amount(rows, *, loan_type, purpose, days) -> float:
    # age_days is supplied relative to the payload's observation time.
    # A 90-day window is deliberately not labelled as three calendar months.
    return sum((
        row["dollar_amount"] for row in rows
        if row["loan_type"] == loan_type and row["purpose"] == purpose
        and 0 <= row["age_days"] < days
    ), 0.0)


if __name__ == "__main__":
    payload = {"loans": [
        {"amount": 700, "loan_type": "credit", "purpose": "shopping", "age_days": 10},
        {"amount": 1400, "loan_type": "home", "purpose": "medical", "age_days": 45},
    ]}
    names = ["loan_count", "loan_credit_shopping_amount_30d", "loan_home_medical_amount_90d"]
    print("Selected:", compute_features(payload, feature_names=names))
    print("All:", compute_features(payload))
