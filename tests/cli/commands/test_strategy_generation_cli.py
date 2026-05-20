from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autonomous_trading_platform.cli.main import build_parser


def _json_objects(output: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    index = 0
    while True:
        start = output.find("{", index)
        if start == -1:
            return objects
        obj, end = decoder.raw_decode(output[start:])
        objects.append(obj)
        index = start + end


def _invoke(argv: list[str], capsys) -> list[dict[str, Any]]:
    parser = build_parser()
    args = parser.parse_args(argv)
    assert args.func(args) == 0
    return _json_objects(capsys.readouterr().out)


def test_list_strategy_types_and_inspect_strategy(capsys) -> None:
    listed = _invoke(["research", "list-strategy-types", "--format", "json"], capsys)[0]
    assert "momentum" in {item["strategy_type"] for item in listed["strategies"]}

    inspected = _invoke(
        ["research", "inspect-strategy", "--strategy-type", "momentum", "--format", "json"],
        capsys,
    )[0]
    assert inspected["strategy_type"] == "momentum"
    assert inspected["parameter_specs"][0]["name"] == "lookback"
    assert inspected["warmup_bars"] == 6


def test_list_components_and_inspect_component(capsys) -> None:
    listed = _invoke(
        [
            "research",
            "list-components",
            "--component-type",
            "indicator",
            "--executable-only",
            "--format",
            "json",
        ],
        capsys,
    )[0]
    assert "momentum" in {item["component_name"] for item in listed["components"]}

    inspected = _invoke(
        ["research", "inspect-component", "--component-name", "momentum", "--format", "json"],
        capsys,
    )[0]
    assert inspected["component_name"] == "momentum"
    assert inspected["is_executable"] is True
    assert inspected["parameters"][0]["name"] == "lookback"


def test_generate_grid_reports_rejections_and_hashes(capsys) -> None:
    payloads = _invoke(
        [
            "research",
            "generate-strategies",
            "--strategy-type",
            "moving_average_crossover",
            "--parameter-space",
            '{"short_window":[5,10],"long_window":[10,20]}',
            "--verbose",
        ],
        capsys,
    )

    summary = payloads[0]
    details = payloads[2]
    assert summary["accepted_count"] == 3
    assert summary["rejected_count"] == 1
    assert len(summary["config_hashes"]) == 3
    assert details["rejected_details"][0]["parameters"] == {
        "short_window": 10,
        "long_window": 10,
    }


def test_generate_random_is_seed_deterministic(capsys) -> None:
    argv = [
        "research",
        "generate-strategies",
        "--strategy-type",
        "momentum",
        "--generator",
        "random",
        "--random-seed",
        "11",
        "--n-samples",
        "5",
    ]
    first = _invoke(argv, capsys)[0]
    second = _invoke(argv, capsys)[0]
    assert first["config_hashes"] == second["config_hashes"]


def test_generate_evolutionary_and_composite(capsys) -> None:
    evolutionary = _invoke(
        [
            "research",
            "generate-strategies",
            "--strategy-type",
            "momentum",
            "--generator",
            "evolutionary",
            "--population-size",
            "4",
            "--generations",
            "1",
        ],
        capsys,
    )[0]
    assert evolutionary["accepted_count"] > 0

    composite_payloads = _invoke(["research", "generate-strategies", "--composite"], capsys)
    assert composite_payloads[0]["strategy_type"] == "composite_rule"
    assert composite_payloads[1]["component_usage"]["threshold"] >= 1


def test_generation_artifact_and_summary_command(tmp_path: Path, capsys) -> None:
    output = tmp_path / "generated.json"
    payloads = _invoke(
        [
            "research",
            "generate-strategies",
            "--composite",
            "--output",
            str(output),
        ],
        capsys,
    )
    assert payloads[-1]["artifact_path"] == str(output)
    assert output.exists()

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["artifact_type"] == "strategy_generation_result"
    assert artifact["configs"][0]["config_hash"]

    summary = _invoke(
        [
            "research",
            "summarize-generated-configs",
            "--input",
            str(output),
            "--show-hashes",
        ],
        capsys,
    )[0]
    assert summary["total_configs"] == 3
    assert summary["config_hashes"] == artifact["config_hashes"]


def test_invalid_component_errors() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["research", "inspect-component", "--component-name", "does_not_exist"]
    )
    with pytest.raises(KeyError):
        args.func(args)
