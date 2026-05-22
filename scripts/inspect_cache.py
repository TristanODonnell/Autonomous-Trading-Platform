#!/usr/bin/env python
"""
Research cache inspection CLI.

Commands
--------
stats       Print hit/miss statistics for generation and simulation caches.
entries     List all cache entries (optionally filtered by cache type).
explain     Explain why a specific key_id is a hit or miss.
clear       Remove all entries from a cache (requires --confirm).

Usage examples
--------------
    python scripts/inspect_cache.py stats
    python scripts/inspect_cache.py stats --output json

    python scripts/inspect_cache.py entries --cache generation
    python scripts/inspect_cache.py entries --cache simulation --output json

    python scripts/inspect_cache.py explain --cache simulation --key-id <sha256>

    python scripts/inspect_cache.py clear --cache generation --confirm

Options
-------
    --cache           generation | simulation | both  (default: both)
    --output          human | json | yaml             (default: human)
    --gen-cache-path  Path to generation cache JSON   (default: data/research/cache/generation/cache.json)
    --sim-cache-path  Path to simulation cache JSON   (default: data/research/cache/simulations/cache.json)
    --key-id          SHA-256 key_id for explain command
    --confirm         Required for the clear command
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_generation_cache(path: Path):
    from autonomous_trading_platform.research.cache.strategy_generation_cache import (
        StrategyGenerationCache,
    )

    return StrategyGenerationCache(persist_path=path if path.exists() else None)


def _load_simulation_cache(path: Path):
    from autonomous_trading_platform.research.cache.simulation_result_cache import (
        SimulationResultCache,
    )

    return SimulationResultCache(persist_path=path if path.exists() else None)


def cmd_stats(args: argparse.Namespace) -> int:
    gen_path = Path(args.gen_cache_path)
    sim_path = Path(args.sim_cache_path)

    result: dict[str, object] = {}

    if args.cache in ("generation", "both"):
        cache = _load_generation_cache(gen_path)
        result["generation"] = cache.stats()

    if args.cache in ("simulation", "both"):
        cache = _load_simulation_cache(sim_path)
        result["simulation"] = cache.stats()

    _print(result, args.output)
    return 0


def cmd_entries(args: argparse.Namespace) -> int:
    gen_path = Path(args.gen_cache_path)
    sim_path = Path(args.sim_cache_path)

    result: dict[str, object] = {}

    if args.cache in ("generation", "both"):
        cache = _load_generation_cache(gen_path)
        result["generation"] = cache.entries()

    if args.cache in ("simulation", "both"):
        cache = _load_simulation_cache(sim_path)
        result["simulation"] = cache.entries()

    _print(result, args.output)
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    if not args.key_id:
        print("ERROR: --key-id is required for the explain command", file=sys.stderr)
        return 1

    sim_path = Path(args.sim_cache_path)
    cache = _load_simulation_cache(sim_path)

    entry = next(
        (e for e in cache.entries() if e.get("key_id") == args.key_id),
        None,
    )

    if entry is None:
        print(f"CACHE MISS  reason=not_found  key_id={args.key_id}")
        return 0

    if args.output == "json":
        print(json.dumps(entry, indent=2, default=str))
    elif args.output == "yaml":
        import yaml  # type: ignore[import]

        print(yaml.dump(entry, default_flow_style=False))
    else:
        print("CACHE HIT")
        print(f"  key_id:          {entry.get('key_id', 'n/a')}")
        print(f"  run_id:          {entry.get('run_id', 'n/a')}")
        print(f"  cached_at:       {entry.get('cached_at', 'n/a')}")
        print(f"  strategy_type:   {entry.get('strategy_type', 'n/a')}")
        print(f"  source_artifact: {entry.get('source_artifact', 'n/a')}")
        print(f"  hit_count:       {entry.get('hit_count', 0)}")
        key = entry.get("key", {})
        print(f"  config_hash:     {key.get('config_hash', 'n/a')[:16]}…")
        print(f"  dataset_version: {key.get('dataset_version', 'n/a')}")
        print(f"  universe_version:{key.get('universe_version', 'n/a')}")
        print(f"  fill_policy:     {key.get('fill_policy', 'n/a')}")
        print(f"  slippage_rate:   {key.get('slippage_rate', 'n/a')}")
        print(f"  commission:      {key.get('commission_per_share', 'n/a')}")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("ERROR: pass --confirm to clear the cache", file=sys.stderr)
        return 1

    gen_path = Path(args.gen_cache_path)
    sim_path = Path(args.sim_cache_path)

    if args.cache in ("generation", "both") and gen_path.exists():
        cache = _load_generation_cache(gen_path)
        cache.clear()
        print(f"Cleared generation cache at {gen_path}")

    if args.cache in ("simulation", "both") and sim_path.exists():
        cache = _load_simulation_cache(sim_path)
        cache.clear()
        print(f"Cleared simulation cache at {sim_path}")

    return 0


def _print(data: object, output: str) -> None:
    if output == "json":
        print(json.dumps(data, indent=2, default=str))
    elif output == "yaml":
        import yaml  # type: ignore[import]

        print(yaml.dump(data, default_flow_style=False))
    else:
        _print_human(data)


def _print_human(data: object, indent: int = 0) -> None:
    prefix = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                print(f"{prefix}{k}:")
                _print_human(v, indent + 1)
            else:
                print(f"{prefix}{k}: {v}")
    elif isinstance(data, list):
        for item in data:
            print(f"{prefix}-")
            _print_human(item, indent + 1)
    else:
        print(f"{prefix}{data}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Research cache inspection tool")
    parser.add_argument("--cache", choices=["generation", "simulation", "both"], default="both")
    parser.add_argument("--output", choices=["human", "json", "yaml"], default="human")
    parser.add_argument(
        "--gen-cache-path",
        default="data/research/cache/generation/cache.json",
    )
    parser.add_argument(
        "--sim-cache-path",
        default="data/research/cache/simulations/cache.json",
    )
    parser.add_argument("--key-id", default=None)
    parser.add_argument("--confirm", action="store_true")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("stats", help="Print cache statistics")
    subparsers.add_parser("entries", help="List cache entries")
    subparsers.add_parser("explain", help="Explain a specific key_id")
    subparsers.add_parser("clear", help="Clear cache entries")

    args = parser.parse_args()

    if args.command == "stats" or args.command is None:
        return cmd_stats(args)
    if args.command == "entries":
        return cmd_entries(args)
    if args.command == "explain":
        return cmd_explain(args)
    if args.command == "clear":
        return cmd_clear(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
