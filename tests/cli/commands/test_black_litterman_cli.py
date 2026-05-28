from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

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


def test_black_litterman_run_cli_parses_yaml_config(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "black_litterman.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "universe_id": "active_universe",
                "universe": ["MomentumStrategy", "MeanReversionStrategy"],
                "covariance_snapshot_id": "cov_cli",
                "covariance_matrix": {
                    "MomentumStrategy": {
                        "MomentumStrategy": 0.04,
                        "MeanReversionStrategy": 0.01,
                    },
                    "MeanReversionStrategy": {
                        "MomentumStrategy": 0.01,
                        "MeanReversionStrategy": 0.09,
                    },
                },
                "benchmark_weights": {
                    "MomentumStrategy": 0.6,
                    "MeanReversionStrategy": 0.4,
                },
                "risk_aversion": 2.5,
                "tau": 0.05,
                "views": [
                    {
                        "type": "relative",
                        "long": "MomentumStrategy",
                        "short": "MeanReversionStrategy",
                        "expected_outperformance": 0.03,
                        "confidence": 0.6,
                    }
                ],
                "persist_artifact": False,
            }
        ),
        encoding="utf-8",
    )

    parser = build_parser()
    args = parser.parse_args(["research", "black-litterman", "run", "--config", str(config_path)])

    assert args.func(args) == 0
    payload = _json_objects(capsys.readouterr().out)[0]
    assert payload["universe_id"] == "active_universe"
    assert payload["diagnostics"]["research_only"] is True
    assert payload["posterior_returns"]
