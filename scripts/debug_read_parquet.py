import pyarrow.parquet as pq

path = r"data\bars\raw\dataset_version=raw_bars_20260424T000840Z_e3e9d8d5\symbol=AAPL\year=2025\month=01\data.parquet"

parquet_file = pq.ParquetFile(path)
table = parquet_file.read()

print(table.schema)
print(table.num_rows)
print(table.to_pandas().head())
