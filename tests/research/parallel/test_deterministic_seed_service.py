from autonomous_trading_platform.research.execution import (
    DeterministicSeedInputs,
    DeterministicSeedService,
)


def test_same_inputs_produce_same_seed() -> None:
    service = DeterministicSeedService()
    inputs = DeterministicSeedInputs(
        base_seed=42,
        experiment_id="exp-1",
        strategy_id="strategy-a",
        config_hash="abc",
        stage_name="cheap",
        window_role="default",
    )

    assert service.derive_seed(inputs) == service.derive_seed(inputs)


def test_different_base_seed_changes_seed() -> None:
    service = DeterministicSeedService()
    seed_a = service.derive_seed(
        DeterministicSeedInputs(base_seed=42, experiment_id="exp-1", strategy_id="strategy-a")
    )
    seed_b = service.derive_seed(
        DeterministicSeedInputs(base_seed=43, experiment_id="exp-1", strategy_id="strategy-a")
    )

    assert seed_a != seed_b


def test_trial_and_fold_identity_affect_seed() -> None:
    service = DeterministicSeedService()
    seed_a = service.derive_seed(
        DeterministicSeedInputs(
            base_seed=42,
            experiment_id="exp-1",
            strategy_id="strategy-a",
            stage_name="walk_forward",
            window_role="fold_0_train",
            fold_id=0,
        )
    )
    seed_b = service.derive_seed(
        DeterministicSeedInputs(
            base_seed=42,
            experiment_id="exp-1",
            strategy_id="strategy-a",
            stage_name="walk_forward",
            window_role="fold_1_train",
            fold_id=1,
        )
    )

    assert seed_a != seed_b
