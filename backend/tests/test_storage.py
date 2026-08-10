from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gtochess.config import Settings
from gtochess.storage import (
    CloudStorage,
    LocalStorage,
    StorageError,
    as_storage,
    build_storage,
)


class FakeS3:
    """Enough of the S3 client to hold objects, including the errors we branch on."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        self.clock = 1_700_000_000.0
        self.modified: dict[str, float] = {}

    def _missing(self, key: str) -> Exception:
        exc = Exception(f"no such key {key}")
        exc.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
        return exc

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise self._missing(Key)

        class When:
            def __init__(self, seconds: float) -> None:
                self._seconds = seconds

            def timestamp(self) -> float:
                return self._seconds

        return {"LastModified": When(self.modified[Key])}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise self._missing(Key)

        class Body:
            def __init__(self, raw: bytes) -> None:
                self._raw = raw

            def read(self) -> bytes:
                return self._raw

        return {"Body": Body(self.objects[Key])}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.puts += 1
        self.clock += 1
        self.objects[Key] = Body
        self.modified[Key] = self.clock

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)
        self.modified.pop(Key, None)


def cloud() -> tuple[CloudStorage, FakeS3]:
    fake = FakeS3()
    return CloudStorage("bucket", prefix="fifty", client=fake), fake


class TestLocal:
    def test_a_missing_record_reads_as_nothing(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        assert store.read("absent.json") is None
        assert store.exists("absent.json") is False
        assert list(store.lines("absent.json")) == []

    def test_it_writes_then_reads_back(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        store.write("a.json", '{"x":1}')
        assert store.read("a.json") == '{"x":1}'

    def test_appending_adds_rather_than_replaces(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        store.append("a.jsonl", "one\n")
        store.append("a.jsonl", "two\n")
        assert [line.strip() for line in store.lines("a.jsonl")] == ["one", "two"]

    def test_blank_lines_are_not_records(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        store.write("a.jsonl", "one\n\n\ntwo\n")
        assert [line.strip() for line in store.lines("a.jsonl")] == ["one", "two"]

    def test_a_missing_record_has_no_stamp(self, tmp_path: Path) -> None:
        assert LocalStorage(tmp_path).stamp("absent") == 0

    def test_writing_moves_the_stamp(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        store.write("a.json", "first")
        before = store.stamp("a.json")
        store.write("a.json", "second")
        assert store.stamp("a.json") >= before > 0

    def test_the_writer_replaces_the_whole_record(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        store.write("a.jsonl", "stale\n")
        with store.writer("a.jsonl") as handle:
            handle.write("fresh\n")
        assert [line.strip() for line in store.lines("a.jsonl")] == ["fresh"]

    def test_it_creates_the_directory_it_was_given(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path / "nested" / "deeper")
        store.write("a.json", "{}")
        assert (tmp_path / "nested" / "deeper" / "a.json").exists()

    def test_deleting_what_is_not_there_is_not_an_error(self, tmp_path: Path) -> None:
        LocalStorage(tmp_path).delete("absent")


class TestCloud:
    def test_a_missing_object_reads_as_nothing(self) -> None:
        store, _ = cloud()
        assert store.read("absent.json") is None
        assert store.exists("absent.json") is False
        assert list(store.lines("absent.json")) == []
        assert store.stamp("absent.json") == 0

    def test_the_prefix_is_part_of_the_key(self) -> None:
        store, fake = cloud()
        store.write("a.json", "{}")
        assert list(fake.objects) == ["fifty/a.json"]

    def test_no_prefix_leaves_the_name_alone(self) -> None:
        fake = FakeS3()
        CloudStorage("bucket", client=fake).write("a.json", "{}")
        assert list(fake.objects) == ["a.json"]

    def test_appending_reads_back_and_puts_whole(self) -> None:
        store, fake = cloud()
        store.append("a.jsonl", "one\n")
        store.append("a.jsonl", "two\n")
        assert [line.strip() for line in store.lines("a.jsonl")] == ["one", "two"]
        assert fake.puts == 2

    def test_the_writer_puts_once_however_many_lines(self) -> None:
        store, fake = cloud()
        with store.writer("a.jsonl") as handle:
            for index in range(500):
                handle.write(f"{index}\n")
        assert fake.puts == 1
        assert len(list(store.lines("a.jsonl"))) == 500

    def test_a_flush_mid_run_does_not_put(self) -> None:
        store, fake = cloud()
        with store.writer("a.jsonl") as handle:
            handle.write("one\n")
            handle.flush()
            assert fake.puts == 0
            handle.write("two\n")
        assert fake.puts == 1

    def test_a_failed_run_writes_nothing(self) -> None:
        store, fake = cloud()
        with pytest.raises(RuntimeError), store.writer("a.jsonl") as handle:
            handle.write("one\n")
            raise RuntimeError("export died")
        assert fake.puts == 0
        assert store.exists("a.jsonl") is False

    def test_writing_moves_the_stamp(self) -> None:
        store, _ = cloud()
        store.write("a.json", "first")
        before = store.stamp("a.json")
        store.write("a.json", "second")
        assert store.stamp("a.json") > before > 0

    def test_a_real_failure_is_not_swallowed_as_absence(self) -> None:
        class Broken(FakeS3):
            def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
                exc = Exception("access denied")
                exc.response = {"Error": {"Code": "AccessDenied"}}  # type: ignore[attr-defined]
                raise exc

        store = CloudStorage("bucket", client=Broken())
        with pytest.raises(StorageError, match="could not read"):
            store.read("a.json")

    def test_deleting_what_is_not_there_is_not_an_error(self) -> None:
        store, _ = cloud()
        store.delete("absent")


class TestSelection:
    def test_a_path_is_a_local_directory(self, tmp_path: Path) -> None:
        assert isinstance(as_storage(tmp_path), LocalStorage)

    def test_a_storage_is_passed_through(self, tmp_path: Path) -> None:
        held = LocalStorage(tmp_path)
        assert as_storage(held) is held

    def test_no_bucket_keeps_the_data_directory(self, tmp_path: Path) -> None:
        chosen = build_storage(Settings(data_dir=tmp_path, s3_bucket=None))
        assert isinstance(chosen, LocalStorage)
        assert chosen.root == tmp_path

    def test_a_bucket_moves_it_off_the_filesystem(self, tmp_path: Path) -> None:
        chosen = build_storage(Settings(data_dir=tmp_path, s3_bucket="fifty", s3_prefix="prod"))
        assert isinstance(chosen, CloudStorage)
        assert chosen.bucket == "fifty"
        assert chosen.key_for("a.json") == "prod/a.json"


class TestWritesAreAtomic:
    def test_a_failed_run_leaves_the_old_record(self, tmp_path: Path) -> None:
        # The failure this guards: an export dying partway used to truncate a
        # 28,000 game history to nothing before the first game arrived.
        store = LocalStorage(tmp_path)
        store.write("games.jsonl", "one\ntwo\nthree\n")
        with pytest.raises(RuntimeError), store.writer("games.jsonl") as handle:
            handle.write("partial\n")
            raise RuntimeError("rate limited")
        assert store.read("games.jsonl") == "one\ntwo\nthree\n"

    def test_a_successful_run_replaces_it(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        store.write("games.jsonl", "stale\n")
        with store.writer("games.jsonl") as handle:
            handle.write("fresh\n")
        assert store.read("games.jsonl") == "fresh\n"

    def test_it_leaves_no_scratch_behind(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        with store.writer("games.jsonl") as handle:
            handle.write("x\n")
        assert [p.name for p in tmp_path.iterdir()] == ["games.jsonl"]

    def test_no_scratch_survives_a_failure_either(self, tmp_path: Path) -> None:
        store = LocalStorage(tmp_path)
        with pytest.raises(RuntimeError), store.writer("games.jsonl") as handle:
            handle.write("x\n")
            raise RuntimeError("boom")
        assert list(tmp_path.iterdir()) == []
