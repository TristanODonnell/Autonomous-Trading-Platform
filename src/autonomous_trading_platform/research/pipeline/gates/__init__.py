"""
Pure gate logic for the robustness stages (regime, stress, overfitting).

Each module here decides pass/fail from already-computed data and has no
awareness of the simulation runner — mirroring aggregation/monte_carlo_aggregator.py
— so every rule is unit-testable with synthetic inputs.
"""
