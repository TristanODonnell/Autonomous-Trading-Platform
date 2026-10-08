"""Object-store access for publishing Parquet dataset versions.

A tiny protocol so the publisher can be tested without S3; ``S3ObjectStore`` is the
production implementation and imports boto3 lazily, so the dependency is only needed
where publishing is switched on.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol


class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...

    def put_file(self, local_path: Path, key: str) -> None: ...

    def put_bytes(
        self, data: bytes, key: str, *, content_type: str = "application/json"
    ) -> None: ...

    def list_keys(self, prefix: str) -> Iterator[str]: ...


class InMemoryObjectStore:
    """Test double: keys → bytes."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def put_file(self, local_path: Path, key: str) -> None:
        self.objects[key] = local_path.read_bytes()

    def put_bytes(self, data: bytes, key: str, *, content_type: str = "application/json") -> None:
        self.objects[key] = data

    def list_keys(self, prefix: str) -> Iterator[str]:
        return iter(sorted(k for k in self.objects if k.startswith(prefix)))


class S3ObjectStore:
    def __init__(
        self, bucket: str, *, region: str | None = None, client: Any | None = None
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3", region_name=region) if region else boto3.client("s3")
        self._bucket = bucket
        self._client = client

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def put_file(self, local_path: Path, key: str) -> None:
        self._client.upload_file(str(local_path), self._bucket, key)

    def put_bytes(self, data: bytes, key: str, *, content_type: str = "application/json") -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def list_keys(self, prefix: str) -> Iterator[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]
