from __future__ import annotations

import argparse
import json
from typing import Any, cast

from autonomous_trading_platform.cli.commands import governance as governance_cmd
from autonomous_trading_platform.cli.main import build_parser


def _json_output(output: str) -> dict[Any, Any]:
    return cast(dict[Any, Any], json.loads(output[output.index("{") :]))


def test_governance_preview_commands_honor_json(capsys) -> None:
    assert (
        governance_cmd.handle_promotion_run(
            argparse.Namespace(actor="governance-cli", enforce=False, json=True)
        )
        == 0
    )
    payload = _json_output(capsys.readouterr().out)
    assert payload["status"] == "preview_only"
    assert payload["enforced"] is False

    assert (
        governance_cmd.handle_health_run(
            argparse.Namespace(persist=False, trigger_source="cli", json=True)
        )
        == 0
    )
    payload = _json_output(capsys.readouterr().out)
    assert payload["status"] == "preview_only"
    assert payload["persisted"] is False

    assert (
        governance_cmd.handle_rules_deactivate(
            argparse.Namespace(
                rule_id="gov_audit_approved_paper_to_approved_live",
                reason="superseded",
                enforce=False,
                json=True,
            )
        )
        == 0
    )
    payload = _json_output(capsys.readouterr().out)
    assert payload["status"] == "preview_only"
    assert payload["enforced"] is False

    assert (
        governance_cmd.handle_audit_supersede(
            argparse.Namespace(
                governance_audit_id="b47d411a-b7e2-4a3e-95b2-6de61ee83883",
                reason="corrected evidence",
                actor="admin",
                superseded_by=None,
                enforce=False,
                json=True,
            )
        )
        == 0
    )
    payload = _json_output(capsys.readouterr().out)
    assert payload["status"] == "preview_only"
    assert payload["enforced"] is False


def test_governance_commands_registered() -> None:
    parser = build_parser()

    commands = [
        ["governance", "promotion", "run", "--actor", "governance-cli", "--json"],
        ["governance", "health", "run", "--trigger-source", "cli", "--json"],
        [
            "governance",
            "rules",
            "deactivate",
            "--rule-id",
            "gov_audit_approved_paper_to_approved_live",
            "--reason",
            "superseded",
            "--json",
        ],
        [
            "governance",
            "audit",
            "supersede",
            "--governance-audit-id",
            "b47d411a-b7e2-4a3e-95b2-6de61ee83883",
            "--reason",
            "corrected evidence",
            "--actor",
            "admin",
            "--json",
        ],
    ]

    for argv in commands:
        args = parser.parse_args(argv)
        assert args.domain == "governance"
        assert hasattr(args, "func")
