import enum


class SpanTimespan(enum.StrEnum):
    # ultra short (micro operations)
    INSTANT = "instant"  # < 1ms

    # small units
    STEP = "step"  # single function / operation
    JOB = "job"  # job execution
    CYCLE = "cycle"  # full orchestration cycle

    # ingestion / processing specific
    BATCH = "batch"
    REQUEST = "request"

    # long running
    BACKFILL = "backfill"
    EXPERIMENT = "experiment"
