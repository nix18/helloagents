"""Behavior tests for multi-CLI update cache broadcast and config cache targeting.

Regression context: cache locations used to be resolved as "the first existing
candidate directory", which bound the cache to whichever CLI sorted first and
left every other CLI permanently missing it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helloagents import _common
from helloagents.core import version_check


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_CONFIG_PATH = REPO_ROOT / "helloagents" / "scripts" / "check_config_quick.py"

# Same wire format _write_update_cache uses; second precision, no sub-second part.
_EXPIRY_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _expiry_after(hours: float) -> str:
    """Render an expires_at value the production reader will accept."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(_EXPIRY_FORMAT)


@contextlib.contextmanager
def _preserved_std_streams():
    """Keep the test runner's stdio intact across a standalone hook import.

    The hook scripts rebind sys.stdout/stderr/stdin to fresh TextIOWrapper
    objects at module scope on Windows.  Those wrappers close the real
    buffers when collected, which would break the runner's own output, so
    they are detached before the originals are restored.  Streams the hook
    left untouched are never detached.
    """
    saved = (sys.stdout, sys.stderr, sys.stdin)
    try:
        yield
    finally:
        for current, original in zip((sys.stdout, sys.stderr, sys.stdin), saved):
            if current is not original and isinstance(current, io.TextIOWrapper):
                with contextlib.suppress(Exception):
                    current.detach()
        sys.stdout, sys.stderr, sys.stdin = saved


def _load_standalone(path: Path):
    """Load a standalone hook script that cannot be imported as a package."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    with _preserved_std_streams():
        spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _patched_home(home: Path):
    """Redirect Path.home() and restore the original descriptor exactly.

    patch.object restores the classmethod object itself; assigning the bound
    method back by hand would silently downgrade Path.home to a plain
    attribute for the rest of the process.
    """
    with mock.patch.object(Path, "home", classmethod(lambda cls: home)):
        yield


class InstalledCliDirsTest(unittest.TestCase):
    """get_installed_cli_dirs() reports every CLI dir, not just the first."""

    def test_returns_every_existing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            expected = {home / ".codex" / "helloagents",
                        home / ".gemini" / "helloagents"}
            for path in expected:
                path.mkdir(parents=True)
            with _patched_home(home):
                dirs = _common.get_installed_cli_dirs()
        self.assertEqual(set(dirs), expected)
        self.assertEqual(len(dirs), len(expected), "no duplicate entries")

    def test_ignores_cli_dir_without_plugin_subdir(self):
        """A CLI config dir alone is not an install; the plugin subdir is."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".claude").mkdir()  # present, but HelloAGENTS not installed
            (home / ".codex" / "helloagents").mkdir(parents=True)
            with _patched_home(home):
                dirs = _common.get_installed_cli_dirs()
        self.assertEqual(dirs, [home / ".codex" / "helloagents"])

    def test_falls_back_when_no_cli_dir_exists(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with _patched_home(home):
                dirs = _common.get_installed_cli_dirs()
        self.assertEqual(dirs, [home / ".helloagents"])


class UpdateCacheBroadcastTest(unittest.TestCase):
    """_write_update_cache broadcasts; _read_update_cache aggregates."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.dirs = [root / name / "helloagents"
                     for name in (".codex", ".claude", ".gemini")]
        for directory in self.dirs:
            directory.mkdir(parents=True)
        self._original = version_check.get_installed_cli_dirs
        version_check.get_installed_cli_dirs = lambda: list(self.dirs)

    def tearDown(self):
        version_check.get_installed_cli_dirs = self._original
        self._tmp.cleanup()

    @property
    def _files(self) -> list[Path]:
        return [d / ".update_cache" for d in self.dirs]

    def _write(self, **kwargs):
        params = {"has_update": False, "local_ver": "2.3.7",
                  "remote_ver": "2.3.7", "branch": "canary",
                  "cache_ttl_hours": 72}
        params.update(kwargs)
        version_check._write_update_cache(**params)

    def _load(self, cache_file: Path) -> dict:
        return json.loads(cache_file.read_text(encoding="utf-8"))

    def _rewrite(self, cache_file: Path, **fields):
        data = self._load(cache_file)
        data.update(fields)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # -- targeting ---------------------------------------------------------

    def test_cache_targets_track_installed_dirs(self):
        """Guards the patch point: writers/readers must resolve dirs per call."""
        self.assertEqual(version_check._iter_update_cache_files(), self._files)

    # -- write path --------------------------------------------------------

    def test_write_reaches_every_directory(self):
        self._write()
        self.assertTrue(all(f.exists() for f in self._files))

    def test_write_payload_is_identical_across_directories(self):
        self._write(has_update=True, remote_ver="2.4.0")
        payloads = [self._load(f) for f in self._files]
        self.assertTrue(all(p == payloads[0] for p in payloads))
        self.assertEqual(payloads[0]["has_update"], True)
        self.assertEqual(payloads[0]["remote_version"], "2.4.0")
        self.assertEqual(payloads[0]["local_version"], "2.3.7")
        self.assertEqual(payloads[0]["branch"], "canary")

    def test_write_continues_past_a_failing_target(self):
        """A broken target must not stop the targets queued behind it.

        The blocker is placed FIRST so a write that aborts on the first
        failure leaves the good directory empty.  The parent being a regular
        file makes mkdir raise on every platform (NotADirectoryError on
        POSIX, FileExistsError on Windows), so the assertion does not depend
        on which errno the OS reports.
        """
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        version_check.get_installed_cli_dirs = lambda: [blocker / "helloagents",
                                                        self.dirs[0]]
        self._write()
        self.assertTrue(self._files[0].exists(),
                        "write aborted instead of skipping the failing target")

    def test_single_cli_writes_exactly_one_file(self):
        version_check.get_installed_cli_dirs = lambda: [self.dirs[0]]
        self._write()
        written = list(Path(self._tmp.name).rglob(".update_cache"))
        self.assertEqual(written, [self._files[0]])

    def test_ttl_none_adopts_furthest_expiry_from_any_directory(self):
        """The furthest expiry may live in a non-first directory."""
        self._write(cache_ttl_hours=72)
        far = _expiry_after(500)
        self._rewrite(self._files[2], expires_at=far)  # only the LAST dir

        self._write(cache_ttl_hours=None, remote_ver="2.3.8")

        for cache_file in self._files:
            with self.subTest(cache_file=cache_file.parent.parent.name):
                data = self._load(cache_file)
                self.assertEqual(data["expires_at"], far)
                self.assertEqual(data["remote_version"], "2.3.8")

    def test_ttl_none_without_prior_cache_does_not_extend_freshness(self):
        self._write(cache_ttl_hours=None)
        self.assertIsNone(version_check._read_update_cache("2.3.7", "canary"))

    # -- read path ---------------------------------------------------------

    def test_read_hits_when_first_directory_is_missing(self):
        """The exact scenario that used to fail for every non-first CLI."""
        self._write(has_update=True, remote_ver="2.4.0")
        self._files[0].unlink()
        cached = version_check._read_update_cache("2.3.7", "canary")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["remote_version"], "2.4.0")
        self.assertTrue(cached["has_update"])

    def test_read_rejects_version_mismatch(self):
        self._write()
        self.assertIsNone(version_check._read_update_cache("9.9.9", "canary"))

    def test_read_rejects_branch_mismatch(self):
        self._write()
        self.assertIsNone(version_check._read_update_cache("2.3.7", "main"))

    def test_read_rejects_expired_entry(self):
        self._write(cache_ttl_hours=0)
        self.assertIsNone(version_check._read_update_cache("2.3.7", "canary"))

    def test_read_picks_newest_last_check(self):
        self._write(remote_ver="OLD")
        self._rewrite(self._files[1],
                      last_check=time.time() + 999, remote_version="NEW")
        cached = version_check._read_update_cache("2.3.7", "canary")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["remote_version"], "NEW")

    def test_read_tolerates_entry_without_last_check(self):
        """A missing timestamp ranks lowest but must not discard the entry."""
        self._write(remote_ver="2.4.0")
        for cache_file in self._files:
            data = self._load(cache_file)
            data.pop("last_check")
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        cached = version_check._read_update_cache("2.3.7", "canary")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["remote_version"], "2.4.0")

    def _assert_only_poisoned_entry_is_skipped(self, poison):
        self._write(remote_ver="GOOD")
        poison(self._files[0])
        cached = version_check._read_update_cache("2.3.7", "canary")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["remote_version"], "GOOD")

    def test_read_skips_corrupted_entry(self):
        self._assert_only_poisoned_entry_is_skipped(
            lambda f: f.write_text("{not json", encoding="utf-8"))

    def test_read_skips_empty_entry(self):
        self._assert_only_poisoned_entry_is_skipped(
            lambda f: f.write_text("", encoding="utf-8"))

    def test_read_skips_entry_without_expires_at(self):
        def poison(cache_file: Path):
            data = self._load(cache_file)
            data.pop("expires_at")
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        self._assert_only_poisoned_entry_is_skipped(poison)

    def test_read_skips_entry_with_unparseable_expires_at(self):
        self._assert_only_poisoned_entry_is_skipped(
            lambda f: self._rewrite(f, expires_at="not-a-timestamp"))

    def test_read_skips_entry_with_non_numeric_last_check(self):
        self._assert_only_poisoned_entry_is_skipped(
            lambda f: self._rewrite(f, last_check="yesterday"))

    def test_read_returns_none_when_nothing_is_installed(self):
        version_check.get_installed_cli_dirs = lambda: []
        self.assertIsNone(version_check._read_update_cache("2.3.7", "canary"))


class ConfigCheckCacheTargetingTest(unittest.TestCase):
    """The config-check cache is per-CLI state and must follow cli_name."""

    @classmethod
    def setUpClass(cls):
        cls.module = _load_standalone(CHECK_CONFIG_PATH)

    def test_known_cli_resolves_to_its_own_directory(self):
        home = Path.home()
        self.assertEqual(self.module._cli_helloagents_dir("claude"),
                         home / ".claude" / "helloagents")
        self.assertEqual(self.module._cli_helloagents_dir("codex"),
                         home / ".codex" / "helloagents")

    def test_directory_differs_per_cli(self):
        self.assertNotEqual(self.module._cli_helloagents_dir("claude"),
                            self.module._cli_helloagents_dir("codex"))

    def test_unknown_cli_falls_back(self):
        resolved = self.module._cli_helloagents_dir("unknown")
        self.assertEqual(resolved, Path.home() / ".helloagents")

    def test_cache_roundtrip_creates_missing_parents(self):
        with tempfile.TemporaryDirectory() as td:
            cache_file = Path(td) / "nested" / ".config_check_cache"
            self.module.update_cache(cache_file, 12345.75)
            self.assertTrue(cache_file.exists())
            self.assertEqual(self.module.get_cached_mtime(cache_file), 12345.75)

    def test_missing_cache_reads_as_zero(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / ".config_check_cache"
            self.assertEqual(self.module.get_cached_mtime(missing), 0.0)

    def test_unparseable_cache_reads_as_zero(self):
        """A garbled cache must degrade to "stale", never crash the hook."""
        with tempfile.TemporaryDirectory() as td:
            cache_file = Path(td) / ".config_check_cache"
            cache_file.write_text("not-a-float", encoding="utf-8")
            self.assertEqual(self.module.get_cached_mtime(cache_file), 0.0)

    def test_update_cache_swallows_unwritable_target(self):
        """The hook must survive an unwritable cache path without raising."""
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            target = blocker / "nested" / ".config_check_cache"
            self.module.update_cache(target, 1.0)  # must not raise
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
