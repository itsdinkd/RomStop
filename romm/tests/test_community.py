"""Tests for RomStop's ratings, favs and install counts in RomM. Run: python -m unittest discover romm/tests"""

import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/community.py"


class _Res:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.data


class _Client:
    def __init__(self, data):
        self.data, self.gets, self.posts = data, 0, []

    async def get(self, url, **_):
        self.gets += 1
        return _Res(self.data)

    async def post(self, url, content=None, headers=None, **_):
        self.posts.append((url, json.loads(content), headers))
        return _Res({"ok": True})


def _load(tmp: Path, client: _Client):
    index_mod = types.SimpleNamespace(ROMSHELF_DIR=tmp, ROMSHELF_SITE_URL="https://romstop.pages.dev/", USER_AGENT="test")
    stubs = {
        "handler": types.ModuleType("handler"),
        "handler.romshelf": types.ModuleType("handler.romshelf"),
        "handler.romshelf.index": index_mod,
        "logger": types.ModuleType("logger"),
        "logger.logger": types.SimpleNamespace(log=types.SimpleNamespace(warning=lambda *_: None)),
        "utils": types.ModuleType("utils"),
        "utils.context": types.SimpleNamespace(ctx_httpx_client=types.SimpleNamespace(get=lambda: client)),
    }
    patcher = mock.patch.dict(sys.modules, stubs)
    patcher.start()
    spec = importlib.util.spec_from_file_location("handler.romshelf.community", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, patcher


def _hacks():
    return [
        {"id": "smwc-1", "ratings": [{"source": "SMW Central", "score": 4.7, "count": 46}], "rating": {"score": 4.7, "count": 46}, "downloads": None},
        {"id": "rhdn-2", "ratings": [], "rating": None, "favs": 8, "downloads": 4464},
        {"id": "pc-3", "ratings": []},
    ]


class CommunityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = _Client({"enabled": True, "stats": {"smwc-1": [5, 1, 2, 0, 0], "rhdn-2": [9, 2, 3, 2, 1], "gone": [1, 1, 0, 0, 0]}})
        self.mod, self.patcher = _load(Path(self.tmp.name), self.client)

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_merges_like_the_site(self):
        hacks = _hacks()
        self.mod.apply_stats(hacks, self.mod.parse_stats(self.client.data))
        smwc, rhdn, pc = hacks
        # 46 SMW Central ratings at 4.7 plus one RomStop 5 = 47 ratings at 4.71.
        self.assertEqual(smwc["rating"], {"score": 4.71, "count": 47})
        self.assertEqual(smwc["ratings"][-1], {"source": "RomStop", "score": 5.0, "display": "5.0 / 5", "count": 1})
        self.assertEqual(smwc["favs"], 2)
        # RHDN: no scraped score, so RomStop's 4.5 is the headline; favs add up; site downloads + RomM installs add up.
        self.assertEqual(rhdn["rating"], {"score": 4.5, "count": 2})
        self.assertEqual((rhdn["favs"], rhdn["downloads"]), (11, 4467))
        # No RomStop stats: untouched.
        self.assertEqual((pc["ratings"], pc.get("rating"), pc.get("favs")), ([], None, None))

    def test_applying_again_starts_from_the_scraped_numbers(self):
        hacks = _hacks()
        stats = self.mod.parse_stats(self.client.data)
        self.mod.apply_stats(hacks, stats)
        self.mod.apply_stats(hacks, stats)
        self.assertEqual(hacks[1]["favs"], 11)
        self.assertEqual(len(hacks[0]["ratings"]), 2)
        self.mod.apply_stats(hacks, {})
        self.assertEqual((hacks[1]["favs"], hacks[1]["downloads"], len(hacks[0]["ratings"])), (8, 4464, 1))
        self.assertNotIn("_scraped", self.mod.public(hacks[0]))

    def test_bad_answers_give_no_stats(self):
        self.assertEqual(self.mod.parse_stats({"enabled": False, "stats": {"a": [1, 1, 1, 1]}}), {})
        self.assertEqual(self.mod.parse_stats("nope"), {})
        # Old four-entry stats still read; junk entries are skipped or zeroed.
        self.assertEqual(
            self.mod.parse_stats({"enabled": True, "stats": {"a": [4, 1, 0, 3], "b": "x", "c": [1], "d": [-1, "2", True, 1, 1]}}),
            {"a": (4.0, 1, 0, 3), "d": (0.0, 0, 0, 2)},
        )

    def test_apply_fetches_once_per_ttl(self):
        index = {"hacks": _hacks()}
        asyncio.run(self.mod.apply(index))
        asyncio.run(self.mod.apply(index))
        self.assertEqual(self.client.gets, 1)
        self.assertEqual(index["hacks"][1]["favs"], 11)
        # A newly fetched index gets the stats too.
        fresh = {"hacks": _hacks()}
        asyncio.run(self.mod.apply(fresh))
        self.assertEqual((self.client.gets, fresh["hacks"][1]["favs"]), (1, 11))
        self.mod.invalidate()
        asyncio.run(self.mod.apply(fresh))
        self.assertEqual(self.client.gets, 2)

    def test_reports_installs_with_a_stable_client_id(self):
        async def go():
            self.mod.report_install("smwc-1")
            self.mod.report_install("rhdn-2")
            await asyncio.gather(*list(self.mod._tasks))

        asyncio.run(go())
        (url, a, headers), (_, b, _) = self.client.posts
        self.assertEqual(url, "https://romstop.pages.dev/api/community")
        self.assertEqual((a["hack"], a["install"]), ("smwc-1", True))
        self.assertEqual(a["client"], b["client"])
        self.assertEqual((Path(self.tmp.name) / "client_id").read_text(), a["client"])
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_reporting_can_be_turned_off(self):
        async def go():
            with mock.patch.dict("os.environ", {"ROMSTOP_SHARE_INSTALLS": "0"}):
                self.mod.report_install("smwc-1")
            self.assertEqual(self.mod._tasks, set())

        asyncio.run(go())
        self.assertEqual(self.client.posts, [])


if __name__ == "__main__":
    unittest.main()
