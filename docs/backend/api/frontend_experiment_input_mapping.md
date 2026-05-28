# Frontend Experiment Input Mapping

Mapping version: `story-57.v1`

The experiments API accepts stable frontend inputs:

- `strategy_type`: `momentum`, `mean_reversion`, `breakout`, `pairs`
- `risk_level`: `low`, `medium`, `high`
- `time_horizon`: `1w`, `1m`, `3m`, `1y`

The versioned mapping layer expands those inputs into internal engine fields:

- `dataset_version`
- `universe`
- `parameter_grid`
- `filter_thresholds`
- `stage_configuration`

The implementation lives in `application/services/experiment_input_mapping.py`.
When engine internals change, add a new mapping version and keep older versions
readable from persisted experiment metadata so frontend inputs remain stable.
