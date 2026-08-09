from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from gtochess.config import Settings, get_settings

if TYPE_CHECKING:
    from types import TracebackType

_storage: Storage | None = None


class StorageError(RuntimeError):
    pass


class LineWriter(Protocol):
    # Returns object rather than None so an open file satisfies it unchanged.
    def write(self, line: str, /) -> object: ...

    def flush(self) -> None: ...


class Storage(Protocol):
    """A flat namespace of text records.

    Every store in the application is a JSONL file read whole and appended a
    line at a time, which is the whole surface below. Names are keys, not paths:
    nothing here nests, so a bucket and a directory hold the same thing.
    """

    def exists(self, name: str) -> bool: ...

    def stamp(self, name: str) -> int: ...

    def read(self, name: str) -> str | None: ...

    def lines(self, name: str) -> Iterator[str]: ...

    def write(self, name: str, text: str, *, private: bool = False) -> None: ...

    def append(self, name: str, chunk: str) -> None: ...

    def writer(self, name: str) -> AbstractContextManager[LineWriter]: ...

    def delete(self, name: str) -> None: ...


class LocalStorage:
    """A directory on any mounted filesystem: local disk, NFS, EFS."""

    def __init__(self, directory: Path | str) -> None:
        self._root = Path(directory)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, name: str) -> Path:
        return self._root / name

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()

    def stamp(self, name: str) -> int:
        path = self.path_for(name)
        return path.stat().st_mtime_ns if path.exists() else 0

    def read(self, name: str) -> str | None:
        path = self.path_for(name)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"could not read {name}: {exc}") from exc

    def lines(self, name: str) -> Iterator[str]:
        path = self.path_for(name)
        if not path.exists():
            return
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield line

    def write(self, name: str, text: str, *, private: bool = False) -> None:
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if private:
            # Best effort on platforms with POSIX permissions, a no-op on Windows.
            with suppress(OSError):
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def append(self, name: str, chunk: str) -> None:
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(chunk)

    @contextmanager
    def writer(self, name: str) -> Iterator[LineWriter]:
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            yield handle

    def delete(self, name: str) -> None:
        self.path_for(name).unlink(missing_ok=True)


class _BufferedWriter:
    """Collects a whole object, because a put is the only way to create one."""

    def __init__(self, storage: CloudStorage, name: str) -> None:
        self._storage = storage
        self._name = name
        self._parts: list[str] = []

    def write(self, line: str) -> None:
        self._parts.append(line)

    def flush(self) -> None:
        """Nothing to do: an object exists only once it is put, whole."""

    def __enter__(self) -> _BufferedWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._storage.write(self._name, "".join(self._parts))


class CloudStorage:
    """An S3 prefix.

    S3 has no append, so `append` reads the object back and puts it whole. That
    is right for the position stores, which are small and written in batches,
    and wrong for a game import, which is why `writer` exists: it buffers the
    run and puts once.
    """

    def __init__(self, bucket: str, *, prefix: str = "", client: Any | None = None) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = client

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3  # type: ignore[import-untyped]

            self._client = boto3.client("s3")
        return self._client

    def key_for(self, name: str) -> str:
        return f"{self._prefix}/{name}" if self._prefix else name

    def _head(self, name: str) -> dict[str, Any] | None:
        try:
            head: dict[str, Any] = self.client.head_object(
                Bucket=self._bucket, Key=self.key_for(name)
            )
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise StorageError(f"could not stat {name}: {exc}") from exc
        return head

    def exists(self, name: str) -> bool:
        return self._head(name) is not None

    def stamp(self, name: str) -> int:
        head = self._head(name)
        if head is None:
            return 0
        modified = head.get("LastModified")
        return int(modified.timestamp() * 1_000_000_000) if modified else 0

    def read(self, name: str) -> str | None:
        try:
            response = self.client.get_object(Bucket=self._bucket, Key=self.key_for(name))
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise StorageError(f"could not read {name}: {exc}") from exc
        body: bytes = response["Body"].read()
        return body.decode("utf-8")

    def lines(self, name: str) -> Iterator[str]:
        text = self.read(name)
        if text is None:
            return
        for line in text.splitlines():
            if line.strip():
                yield line + "\n"

    def write(self, name: str, text: str, *, private: bool = False) -> None:
        # private is carried by the bucket policy rather than the object, so
        # there is nothing to set per key.
        try:
            self.client.put_object(
                Bucket=self._bucket,
                Key=self.key_for(name),
                Body=text.encode("utf-8"),
                ContentType="application/json",
            )
        except Exception as exc:
            raise StorageError(f"could not write {name}: {exc}") from exc

    def append(self, name: str, chunk: str) -> None:
        held = self.read(name) or ""
        self.write(name, held + chunk)

    def writer(self, name: str) -> AbstractContextManager[LineWriter]:
        return _BufferedWriter(self, name)

    def delete(self, name: str) -> None:
        try:
            self.client.delete_object(Bucket=self._bucket, Key=self.key_for(name))
        except Exception as exc:
            if _is_missing(exc):
                return
            raise StorageError(f"could not delete {name}: {exc}") from exc


def _is_missing(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def build_storage(settings: Settings | None = None) -> Storage:
    settings = settings or get_settings()
    if settings.s3_bucket:
        return CloudStorage(settings.s3_bucket, prefix=settings.s3_prefix)
    return LocalStorage(settings.data_dir)


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = build_storage()
    return _storage


def set_storage(storage: Storage | None) -> None:
    global _storage
    _storage = storage


def as_storage(target: Storage | Path | str) -> Storage:
    if isinstance(target, (Path, str)):
        return LocalStorage(target)
    return target
