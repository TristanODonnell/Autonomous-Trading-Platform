import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc


def compute_table_checksum(table: pa.Table) -> str:
    schema_without_metadata = table.schema.remove_metadata()
    normalized_table = table.cast(schema_without_metadata)

    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, normalized_table.schema) as writer:
        writer.write_table(normalized_table)

    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def compute_file_checksum(path: str | Path) -> str:
    file_path = Path(path)
    hasher = hashlib.sha256()

    with file_path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            hasher.update(chunk)

    return hasher.hexdigest()
