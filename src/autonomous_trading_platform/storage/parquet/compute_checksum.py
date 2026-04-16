import hashlib

import pyarrow as pa
import pyarrow.ipc as ipc


def compute_table_checksum(table: pa.Table) -> str:
    schema_without_metadata = table.schema.remove_metadata()
    normalized_table = table.cast(schema_without_metadata)

    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, normalized_table.schema) as writer:
        writer.write_table(normalized_table)

    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()
