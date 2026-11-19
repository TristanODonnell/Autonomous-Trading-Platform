"""Every model file is imported by the models package.

A SQLAlchemy model registers its table in ``Base.metadata`` when its module is imported,
and only then. The test database (``create_all``) and ``alembic revision --autogenerate``
both work from that metadata, so a model file the package does not import is a table the
test database lacks and a table autogenerate would propose to drop.

The first test reads the package's source instead of asking what is loaded: in a full run
some other test has usually imported a missing model already, which hides the fault.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import autonomous_trading_platform.storage.sor.models as models_package

_PACKAGE_DIR = Path(models_package.__file__).parent
# Module files that declare no table.
_NOT_MODELS = {"__init__", "base"}
_TABLE_NAME = re.compile(r"""^\s*__tablename__\s*=\s*["']([^"']+)["']""", re.MULTILINE)


def _model_files() -> set[str]:
    return {path.stem for path in _PACKAGE_DIR.glob("*.py")} - _NOT_MODELS


def _package_tree() -> ast.Module:
    return ast.parse((_PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8"))


def _imported_by_the_package() -> set[str]:
    """Sibling modules the package imports: ``from . import x`` and ``from .x import y``."""
    names: set[str] = set()
    for node in _package_tree().body:
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module is None:
                names.update(alias.name for alias in node.names)
            else:
                names.add(node.module.split(".")[0])
    return names


def _names_bound_by_the_package() -> set[str]:
    """What ``from . import x as y`` leaves in the package: ``y`` (or ``x`` with no alias)."""
    return {
        alias.asname or alias.name
        for node in _package_tree().body
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None
        for alias in node.names
    }


def _listed_in_all() -> set[str]:
    for node in _package_tree().body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError("the models package defines no __all__")


def test_every_model_file_is_imported_by_the_models_package() -> None:
    missing = sorted(_model_files() - _imported_by_the_package())

    assert missing == [], (
        f"model file(s) not imported by storage/sor/models/__init__.py: {missing}. Add "
        "`from . import <name> as <name>` for each, or the test database and Alembic "
        "autogenerate will not see their tables."
    )


def test_the_package_imports_no_module_that_is_not_there() -> None:
    assert sorted(_imported_by_the_package() - _model_files() - _NOT_MODELS) == []


def test_all_lists_what_the_package_imports_and_nothing_else() -> None:
    # One module is imported under an alias (``ticker_lifecycle_event as
    # ticker_lifecycle_events``), so the comparison is on the names the package binds.
    assert _listed_in_all() == _names_bound_by_the_package() | {"Base"}


def test_loaded_the_way_alembic_loads_them_every_declared_table_is_known() -> None:
    # Alembic's env.py imports ``Base`` from ``models.base`` and nothing else. That import
    # runs the package first, so the package's imports decide which tables Alembic sees.
    # Asked in a fresh interpreter: this process has imported models on its own already.
    declared = {
        name
        for stem in _model_files()
        for name in _TABLE_NAME.findall((_PACKAGE_DIR / f"{stem}.py").read_text(encoding="utf-8"))
    }
    assert declared, "no __tablename__ found in any model file"

    script = (
        "from autonomous_trading_platform.storage.sor.models.base import Base\n"
        "print('\\n'.join(sorted(Base.metadata.tables)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    known = {line.strip() for line in result.stdout.splitlines() if line.strip()}

    assert sorted(declared - known) == []
