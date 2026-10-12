import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import store
from core.validation import InputError


class ScanArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cache = patch.object(store, "SCAN_DIR", str(self.root / "scan"))
        cache.start()
        self.addCleanup(cache.stop)

    def scan(self, generated="2026-10-12 12:00:00", close=10):
        return {"generated": generated, "freq": "day", "hit": 1, "total_scanned": 10,
                "rows": [{"code": "600000", "symbol": "sh600000", "close": close,
                          "buy": [{"type": "一类买点", "fresh": 0, "valid": True}]}]}

    def test_multiple_scans_on_one_date_are_independent_and_newest_first(self):
        first = self.scan("2026-10-12 09:30:00", close=10)
        second = self.scan("2026-10-12 15:00:00", close=12)
        first_rows = copy.deepcopy(first["rows"])
        store.save_scan(first)
        store.save_scan(second)
        self.assertEqual([item["id"] for item in store.list_scans()],
                         [second["scan_id"], first["scan_id"]])
        self.assertEqual(store.load_scan(first["scan_id"])["rows"], first_rows)
        self.assertEqual(store.load_scan()["rows"], second["rows"])
        self.assertEqual(store.list_scans()[0],
                         {"id": second["scan_id"], "generated": second["generated"],
                          "date": "2026-10-12", "freq": "day", "hit": 1, "total_scanned": 10})

    def test_same_second_scans_do_not_overwrite(self):
        scans = [self.scan(close=10 + i) for i in range(3)]
        for payload in scans:
            store.save_scan(payload)
        self.assertEqual([p["scan_id"] for p in scans],
                         ["20261012-120000", "20261012-120000-001", "20261012-120000-002"])
        self.assertEqual([store.load_scan(p["scan_id"])["rows"][0]["close"] for p in scans],
                         [10, 11, 12])
        self.assertEqual(store.load_scan(), scans[-1])
        self.assertEqual(store.list_scans()[0]["id"], scans[-1]["scan_id"])

    def test_concurrent_same_second_saves_keep_every_snapshot(self):
        scans = [self.scan(close=10 + i) for i in range(4)]
        errors = []

        def save(payload):
            try:
                store.save_scan(payload)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=save, args=(payload,)) for payload in scans]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors)
        self.assertEqual(len({payload["scan_id"] for payload in scans}), 4)
        for payload in scans:
            self.assertEqual(store.load_scan(payload["scan_id"]), payload)

    def test_default_retention_keeps_more_than_eight_scans(self):
        for hour in range(12):
            store.save_scan(self.scan("2026-10-12 %02d:00:00" % hour))
        self.assertEqual(len(store.list_scans()), 12)
        self.assertIsNotNone(store.load_scan("20261012-000000"))

    def test_explicit_retention_remains_available(self):
        for hour in range(5):
            store.save_scan(self.scan("2026-10-12 %02d:00:00" % hour), keep_snapshots=2)
        self.assertEqual([item["id"] for item in store.list_scans()],
                         ["20261012-040000", "20261012-030000"])
        self.assertIsNone(store.load_scan("20261012-020000"))

    def test_invalid_ids_reject_paths_and_dates_before_reading(self):
        for invalid in ("../latest", "latest", "", "20261012-120000/../x", "20261012-120000.json",
                        "20260230-120000", "20261012-250000", "２０２６１０１２-120000", 123):
            with self.subTest(invalid=invalid), self.assertRaises(InputError):
                store.load_scan(invalid)
            with self.subTest(path=invalid), self.assertRaises(InputError):
                store.scan_path(invalid)
        self.assertIsNone(store.load_scan("20261010-120000"))

    def test_listing_ignores_corrupt_and_unrelated_files(self):
        saved = self.scan()
        store.save_scan(saved)
        scan_dir = self.root / "scan"
        (scan_dir / "20261011-120000.json").write_text("{broken", encoding="utf-8")
        (scan_dir / "20261010-120000.json").write_text("[]", encoding="utf-8")
        (scan_dir / "20261009-120000.json").write_text(json.dumps({"rows": "broken"}), encoding="utf-8")
        for name in ("attempt.json", "unexpected.json", "20260230-120000.json"):
            (scan_dir / name).write_text(json.dumps(self.scan()), encoding="utf-8")
        with patch.object(store.LOG, "warning"):
            self.assertEqual([item["id"] for item in store.list_scans()], [saved["scan_id"]])
            self.assertIsNone(store.load_scan("20261011-120000"))

    def test_legacy_latest_without_snapshot_is_archived_without_changing_rows(self):
        legacy = self.scan("2026-10-09 15:00:00")
        store.write_json(store.scan_path(), legacy)
        self.assertEqual(store.list_scans()[0]["id"], "20261009-150000")
        self.assertEqual(store.load_scan("20261009-150000")["rows"], legacy["rows"])
        newer = self.scan()
        store.save_scan(newer)
        self.assertEqual(len(store.list_scans()), 2)
        self.assertEqual(store.load_scan("20261009-150000")["rows"], legacy["rows"])
        self.assertNotIn("scan_id", legacy)

    def test_legacy_latest_matching_existing_snapshot_is_not_duplicated(self):
        legacy = self.scan()
        store.write_json(store.scan_path("20261012-120000"), legacy)
        store.write_json(store.scan_path(), legacy)
        self.assertEqual(len(store.list_scans()), 1)
        self.assertEqual(store.load_scan()["scan_id"], "20261012-120000")

    def test_legacy_latest_conflicting_with_snapshot_gets_separate_id(self):
        older, latest = self.scan(close=9), self.scan(close=10)
        store.write_json(store.scan_path("20261012-120000"), older)
        store.write_json(store.scan_path(), latest)
        ids = [item["id"] for item in store.list_scans()]
        self.assertEqual(ids, ["20261012-120000-001", "20261012-120000"])
        self.assertEqual(store.load_scan(ids[0])["rows"], latest["rows"])
        self.assertEqual(store.load_scan(ids[1])["rows"], older["rows"])
        self.assertEqual(store.load_scan()["scan_id"], ids[0])
        self.assertEqual([item["id"] for item in store.list_scans()], ids)

    def test_snapshot_failure_preserves_latest_and_input(self):
        first, failed = self.scan(), self.scan("2026-10-12 15:00:00")
        store.save_scan(first)
        with patch.object(store, "write_json", side_effect=OSError("disk full")), self.assertRaises(OSError):
            store.save_scan(failed)
        self.assertEqual(store.load_scan(), first)
        self.assertEqual(store.load_scan(first["scan_id"]), first)
        self.assertNotIn("scan_id", failed)

    def test_latest_publish_failure_removes_only_this_attempted_snapshot(self):
        first, second = self.scan("2026-10-12 09:00:00"), self.scan()
        store.save_scan(first)
        store.save_scan(second)
        old_listing = store.list_scans()
        failed = self.scan(close=12)
        write = store.write_json
        calls = []

        def fail_second_write(path, payload):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("latest replacement failed")
            return write(path, payload)

        with patch.object(store, "write_json", side_effect=fail_second_write), \
             self.assertRaisesRegex(OSError, "latest replacement failed"):
            store.save_scan(failed)
        self.assertEqual(calls, [store.scan_path("20261012-120000-001"), store.scan_path()])
        self.assertFalse(Path(calls[0]).exists())
        self.assertEqual(store.list_scans(), old_listing)
        self.assertEqual(store.load_scan(), second)
        self.assertEqual(store.load_scan(first["scan_id"]), first)
        self.assertEqual(store.load_scan(second["scan_id"]), second)
        self.assertIsNone(store.load_scan("20261012-120000-001"))
        self.assertNotIn("scan_id", failed)

    def test_failed_legacy_archive_cannot_be_overwritten_by_new_scan(self):
        first = self.scan("2026-10-09 15:00:00")
        store.write_json(store.scan_path(), first)
        with patch.object(store, "write_json", side_effect=OSError("disk full")), \
             patch.object(store.LOG, "warning"), self.assertRaises(OSError):
            store.save_scan(self.scan())
        self.assertEqual(store.read_json(store.scan_path()), first)
        # Read requests still work when a disk cannot accept legacy migration.
        with patch.object(store, "write_json", side_effect=OSError("read only")), \
             patch.object(store.LOG, "warning"):
            self.assertEqual(store.load_scan()["rows"], first["rows"])
            self.assertEqual(store.load_scan("20261009-150000")["rows"], first["rows"])
            self.assertEqual(store.list_scans()[0]["id"], "20261009-150000")

    def test_catalog_reuses_metadata_without_retaining_rows_or_exposing_cache(self):
        scans = [self.scan("2026-10-12 %02d:00:00" % hour) for hour in (9, 10, 11)]
        for payload in scans:
            store.save_scan(payload)
        old_paths = [store.scan_path(payload["scan_id"]) for payload in scans[:-1]]
        with patch.object(store, "read_json", wraps=store.read_json) as read:
            first = store.list_scans()
            first[-1]["hit"] = 999
            second = store.list_scans()
        self.assertEqual(second[-1]["hit"], 1)
        for path in old_paths:
            self.assertEqual(sum(call.args[0] == path for call in read.call_args_list), 1)
        for _, metadata in store._SCAN_METADATA_CACHE.values():
            self.assertNotIn("rows", metadata)
            self.assertEqual(set(metadata), {"id", "generated", "date", "freq", "hit", "total_scanned"})

    def test_catalog_metadata_invalidates_on_replace_damage_repair_and_delete(self):
        older, latest = self.scan("2026-10-12 09:00:00"), self.scan()
        store.save_scan(older)
        store.save_scan(latest)
        store.list_scans()
        path = store.scan_path(older["scan_id"])
        info = os.stat(path)
        changed = dict(older, hit=2)
        store.write_json(path, changed)
        # Even an equal-length atomic replacement with its original mtime is detected.
        self.assertEqual(os.stat(path).st_size, info.st_size)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(store.list_scans()[-1]["hit"], 2)
        Path(path).write_text("{broken", encoding="utf-8")
        with patch.object(store.LOG, "warning"):
            self.assertEqual(len(store.list_scans()), 1)
        store.write_json(path, dict(older, hit=3))
        self.assertEqual(store.list_scans()[-1]["hit"], 3)
        os.unlink(path)
        self.assertEqual(len(store.list_scans()), 1)
        self.assertNotIn(os.path.abspath(path), store._SCAN_METADATA_CACHE)

    def test_catalog_negative_cache_skips_reparsing_unchanged_corruption(self):
        store.save_scan(self.scan())
        path = store.scan_path("20261011-090000")
        Path(path).write_text("{broken", encoding="utf-8")
        with patch.object(store.LOG, "warning"), patch.object(store, "read_json", wraps=store.read_json) as read:
            store.list_scans()
            store.list_scans()
        self.assertEqual(sum(call.args[0] == path for call in read.call_args_list), 1)
        store.write_json(path, self.scan("2026-10-11 09:00:00"))
        self.assertEqual(len(store.list_scans()), 2)

    def test_catalog_cache_is_independent_after_switching_scan_directory(self):
        original = self.scan()
        store.save_scan(original)
        self.assertEqual(store.list_scans()[0]["hit"], 1)
        with patch.object(store, "SCAN_DIR", str(self.root / "other")):
            other = dict(self.scan(), hit=2)
            store.save_scan(other)
            self.assertEqual(store.list_scans()[0]["hit"], 2)
        self.assertEqual(store.list_scans()[0]["hit"], 1)


if __name__ == "__main__":
    unittest.main()
