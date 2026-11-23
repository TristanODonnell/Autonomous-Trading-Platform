"""A research job names its bars by dataset version id (research plan R2, the hook
layout, piece 3a). The loader answers what that version is, from its registration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.research_window_dataset_loader import (
    ResearchWindowDatasetLoadError,
    load_research_window_dataset,
)
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    RESEARCH_WINDOW_LIFECYCLE,
    RESEARCH_WINDOW_SOURCE,
)
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions

VERSION = "raw_bars_window_test"
FIRST = date(2024, 10, 1)
LAST = date(2026, 11, 16)


def _register(session: Session, **overrides: Any) -> None:
    """A row as ``atp datasets build-window`` writes it."""
    fields: dict[str, Any] = dict(
        dataset_version_id=VERSION,
        dataset_name="raw_bars",
        created_at=datetime(2026, 11, 18, tzinfo=UTC),
        source=RESEARCH_WINDOW_SOURCE,
        price_basis=PriceBasis.RAW,
        interval=BarInterval.FIVE_MIN,
        schema_version="1.0.0",
        symbol_coverage=3,
        date_coverage_start=FIRST,
        date_coverage_end=LAST,
        validation_status="validated",
        checksum="checksum",
        source_manifest={"pipeline": RESEARCH_WINDOW_SOURCE, "symbols": ["NVDA", "AAPL", "MSFT"]},
        metadata_json={
            "dataset_lifecycle": RESEARCH_WINDOW_LIFECYCLE,
            "excluded_sessions": ["2025-03-10", "2024-12-23"],
        },
    )
    fields.update(overrides)
    session.add(DatasetVersions(**fields))
    session.flush()


class TestARegisteredWindowDataset:
    def test_says_where_its_bars_begin_and_end_and_what_it_holds(self, db_session: Session) -> None:
        _register(db_session)

        dataset = load_research_window_dataset(db_session, VERSION)

        assert dataset.dataset_version_id == VERSION
        assert (dataset.first_day, dataset.last_day) == (FIRST, LAST)
        assert dataset.symbols == ("AAPL", "MSFT", "NVDA")
        assert dataset.excluded_sessions == (date(2024, 12, 23), date(2025, 3, 10))

    def test_a_name_registered_twice_is_held_once(self, db_session: Session) -> None:
        _register(
            db_session,
            source_manifest={
                "pipeline": RESEARCH_WINDOW_SOURCE,
                "symbols": ["MSFT", "AAPL", "MSFT"],
            },
        )

        assert load_research_window_dataset(db_session, VERSION).symbols == ("AAPL", "MSFT")

    def test_no_session_left_out_is_an_empty_list(self, db_session: Session) -> None:
        _register(db_session, metadata_json={"dataset_lifecycle": RESEARCH_WINDOW_LIFECYCLE})

        assert load_research_window_dataset(db_session, VERSION).excluded_sessions == ()

    def test_what_is_returned_cannot_be_changed(self, db_session: Session) -> None:
        _register(db_session)
        dataset = load_research_window_dataset(db_session, VERSION)

        with pytest.raises(ValidationError):
            dataset.last_day = date(2027, 1, 1)  # type: ignore[misc]


class TestRefused:
    def test_a_version_that_is_not_registered(self, db_session: Session) -> None:
        with pytest.raises(ResearchWindowDatasetLoadError, match="is not registered"):
            load_research_window_dataset(db_session, VERSION)

    def test_raw_bars_that_are_not_a_research_window(self, db_session: Session) -> None:
        """A replay's cumulative dataset: validated raw bars, sessions never checked."""
        _register(
            db_session,
            source="platform_backtest",
            metadata_json={"dataset_lifecycle": "platform_backtest_cumulative"},
        )

        with pytest.raises(ResearchWindowDatasetLoadError) as refusal:
            load_research_window_dataset(db_session, VERSION)

        assert "is not a research-window dataset" in str(refusal.value)
        assert "a raw_bars version from platform_backtest" in str(refusal.value)
        assert "atp datasets build-window" in str(refusal.value)

    def test_raw_bars_registered_with_no_metadata(self, db_session: Session) -> None:
        _register(db_session, metadata_json=None)

        with pytest.raises(ResearchWindowDatasetLoadError, match="not a research-window dataset"):
            load_research_window_dataset(db_session, VERSION)

    def test_another_kind_of_dataset_carrying_the_tag(self, db_session: Session) -> None:
        _register(db_session, dataset_name="adjusted_bars")

        with pytest.raises(ResearchWindowDatasetLoadError, match="not a research-window dataset"):
            load_research_window_dataset(db_session, VERSION)

    def test_a_window_dataset_that_is_not_validated(self, db_session: Session) -> None:
        _register(db_session, validation_status="pending")

        with pytest.raises(ResearchWindowDatasetLoadError, match="'pending', not validated"):
            load_research_window_dataset(db_session, VERSION)

    @pytest.mark.parametrize("missing", ["date_coverage_start", "date_coverage_end"])
    def test_a_window_dataset_without_its_first_or_last_day(
        self, db_session: Session, missing: str
    ) -> None:
        _register(db_session, **{missing: None})

        with pytest.raises(ResearchWindowDatasetLoadError, match="without its first and last day"):
            load_research_window_dataset(db_session, VERSION)

    @pytest.mark.parametrize(
        "manifest",
        [
            None,
            {},
            {"symbols": []},
            {"symbols": "AAPL"},
            {"symbols": ["AAPL", ""]},
            {"symbols": [7]},
        ],
    )
    def test_a_window_dataset_without_the_names_it_holds(
        self, db_session: Session, manifest: Any
    ) -> None:
        _register(db_session, source_manifest=manifest)

        with pytest.raises(ResearchWindowDatasetLoadError, match="without the names it holds"):
            load_research_window_dataset(db_session, VERSION)

    @pytest.mark.parametrize("excluded", [["not-a-date"], [20241223], "2024-12-23x"])
    def test_a_left_out_session_that_is_not_a_date(
        self, db_session: Session, excluded: Any
    ) -> None:
        _register(
            db_session,
            metadata_json={
                "dataset_lifecycle": RESEARCH_WINDOW_LIFECYCLE,
                "excluded_sessions": excluded,
            },
        )

        with pytest.raises(ResearchWindowDatasetLoadError, match="session that is not a date"):
            load_research_window_dataset(db_session, VERSION)
