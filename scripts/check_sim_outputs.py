import duckdb

run_id = "d01792e4-4b24-4fe4-ae4d-7c37f34b26e7"

datasets = ["trade_logs", "equity_curve", "per_bar_metrics", "positions"]

for name in datasets:
    path = f"data/simulations/{name}/**/*.parquet"
    query = """
        SELECT count(*)
        FROM read_parquet(?)
        WHERE run_id = ?
    """
    count = duckdb.execute(query, [path, run_id]).fetchone()[0]
    print(f"{name}: {count}")
