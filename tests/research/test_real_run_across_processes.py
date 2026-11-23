"""The same experiment, run in two separate processes, produces the same artifacts (plan R7).

"Same seed, same result" inside one process says little: both runs share the
interpreter's state. Two processes share nothing, and each is given a different
PYTHONHASHSEED, so anything that leans on the order of a set or on ``hash()`` of
a string comes out differently in each. Caches, resume and run ids all assume
the answer is the same.

What is compared (tests/research/real_run_child.py writes it): who entered and
survived each of the hook's six stages, every simulation's run id and numbers,
the Monte Carlo and overfitting numbers, and a checksum of the rows of every
output file under its path.

Run with:
    python -m pytest tests/research/test_real_run_across_processes.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.research.real_run_support import short_data_root

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_DATASETS = {"equity_curve", "per_bar_metrics", "positions", "signal_log", "trade_logs"}
_TIMEOUT_SECONDS = 600


def _start(data_root: Path, *, hash_seed: str) -> subprocess.Popen[str]:
    data_root.mkdir()
    env = {
        **os.environ,
        "PYTHONHASHSEED": hash_seed,
        # Never the developer's database or data folder, whatever the shell has set.
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite:///:memory:",
        "NO_LIVE_TRADING": "true",
        "DATA_ROOT": str(data_root),
        "PARQUET_DATA_ROOT": str(data_root),
    }
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.research.real_run_child",
            str(data_root),
            str(data_root / "summary.json"),
        ],
        cwd=_REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(process: subprocess.Popen[str], data_root: Path) -> dict[str, Any]:
    _, errors = process.communicate(timeout=_TIMEOUT_SECONDS)
    assert process.returncode == 0, f"the run in {data_root.name} failed:\n{errors[-3000:]}"
    summary: dict[str, Any] = json.loads((data_root / "summary.json").read_text(encoding="utf-8"))
    return summary


def _as_text(value: Any) -> str:
    # Text, so that a NaN compares equal to a NaN.
    return json.dumps(value, indent=1, sort_keys=True)


def test_two_processes_produce_identical_artifacts() -> None:
    with short_data_root() as folder:
        roots = [folder / "a", folder / "b"]
        # Started together, so the test costs one run of wall-clock time, not two.
        processes = [
            _start(root, hash_seed=seed) for root, seed in zip(roots, ("1", "2"), strict=True)
        ]
        first, second = (
            _finish(process, root) for process, root in zip(processes, roots, strict=True)
        )

    # The run is a real one: six stages, strategies dropped along the way,
    # simulations recorded, and files of all five kinds on disk.
    assert len(first["stages"]) == 6
    assert first["stages"][0]["entered"] == 3
    assert 0 < len(first["final_survivors"]) < 3
    assert len(first["simulations"]) >= 3
    assert any("monte_carlo_pass_rate" in numbers for numbers in first["gate_numbers"].values())
    assert {path.split("/")[1] for path in first["files"]} == _OUTPUT_DATASETS

    # And the second process, which shares nothing with the first, agrees on all of it.
    assert set(second) == set(first)
    for part in ("stages", "final_survivors", "simulations", "gate_numbers"):
        assert _as_text(second[part]) == _as_text(first[part]), f"{part} differ between processes"
    assert sorted(second["files"]) == sorted(first["files"]), "the two runs wrote different files"
    differing = [path for path in first["files"] if first["files"][path] != second["files"][path]]
    assert differing == [], "files whose rows differ between processes"
