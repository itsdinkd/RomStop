"""Tests for RomStop's Send with FTP. Run: python -m unittest discover romm/tests"""

import ftplib
import importlib.util
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Imported here, not first inside _load's patched sys.modules, which would drop its submodules again on exit.
import cryptography.fernet  # noqa: F401

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/ftp.py"


def _load(tmp: Path, secret: str = "secret-one"):
    stubs = {
        "config": types.SimpleNamespace(ROMM_AUTH_SECRET_KEY=secret),
        "handler": types.ModuleType("handler"),
        "handler.romshelf": types.ModuleType("handler.romshelf"),
        "handler.romshelf.index": types.SimpleNamespace(ROMSHELF_DIR=tmp),
    }
    with mock.patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("handler.romshelf.ftp", _PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class FakeFtp:
    """An FTP server in memory: folders, files, and the sign-in it accepts."""

    instances: list["FakeFtp"] = []

    def __init__(self, user="anonymous", password="", dirs=("/content", "/content/snes"), files=None, fail_stor=False):
        self.user, self.password = user, password
        self.dirs = set(dirs)
        self.files: dict[str, bytes] = dict(files or {})
        self.fail_stor = fail_stor
        self.logged_in = None
        self.connected_to = None
        self.pasv = None
        self.closed = False
        FakeFtp.instances.append(self)

    def __call__(self):  # used as the factory
        return self

    def connect(self, host, port, timeout=None):
        if host == "offline":
            raise ConnectionRefusedError(111, "Connection refused")
        self.connected_to = (host, port)

    def login(self, user, password):
        if (user, password) != (self.user, self.password):
            raise ftplib.error_perm("530 Login incorrect.")
        self.logged_in = user

    def set_pasv(self, value):
        self.pasv = value

    def getwelcome(self):
        return "220 fake"

    def cwd(self, path):
        if path not in self.dirs:
            raise ftplib.error_perm("550 No such directory")

    def mkd(self, path):
        if path in self.dirs:
            raise ftplib.error_perm("550 Exists")
        self.dirs.add(path)
        return path

    def voidcmd(self, cmd):
        return "200 ok"

    def size(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 No such file")
        return len(self.files[path])

    def mlsd(self, path, facts=None):
        prefix = path.rstrip("/") + "/"
        for d in sorted(self.dirs):
            if d.startswith(prefix) and "/" not in d[len(prefix):]:
                yield d[len(prefix):], {"type": "dir"}

    def nlst(self, path):
        return [name for name, _ in self.mlsd(path)]

    def storbinary(self, cmd, fp, blocksize=8192, callback=None):
        path = cmd.split(" ", 1)[1]
        if self.fail_stor:
            raise ftplib.error_perm("553 Permission denied")
        if os.path.dirname(path) not in self.dirs:
            raise ftplib.error_perm("553 No such folder")
        data = b""
        while chunk := fp.read(blocksize):
            data += chunk
            if callback:
                callback(chunk)
        self.files[path] = data

    def rename(self, src, dst):
        if src not in self.files:
            raise ftplib.error_perm("550 No such file")
        self.files[dst] = self.files.pop(src)

    def quit(self):
        self.closed = True


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mod = _load(self.tmp)

    def test_password_is_encrypted_never_returned_and_kept_when_left_out(self):
        out = self.mod.save({"enabled": True, "host": "192.168.1.50", "username": "me", "password": "hunter2"})
        self.assertNotIn("password", out)
        self.assertNotIn("password_enc", out)
        self.assertTrue(out["password_set"])
        raw = (self.tmp / "ftp.json").read_text()
        self.assertNotIn("hunter2", raw)
        self.assertEqual(stat.S_IMODE((self.tmp / "ftp.json").stat().st_mode), 0o600)
        # Saving the form again without a password keeps the saved one.
        self.mod.save({"port": 2121})
        s = self.mod.load()
        self.assertEqual(s["port"], 2121)
        self.assertEqual(self.mod._decrypt(s["password_enc"]), "hunter2")
        self.mod.save({"clear_password": True})
        self.assertFalse(self.mod.public()["password_set"])

    def test_a_new_secret_key_asks_for_the_password_again(self):
        self.mod.save({"host": "ps5", "username": "me", "password": "pw"})
        other = _load(self.tmp, secret="secret-two")
        with self.assertRaises(other.FtpError) as ctx:
            other._decrypt(other.load()["password_enc"])
        self.assertEqual(ctx.exception.code, "password_unreadable")

    def test_bad_settings_are_refused(self):
        for bad in (
            {"host": "ftp://ps5/content"},
            {"port": 70000},
            {"base_path": "/content/../etc"},
            {"folders": {"snes": "../snes"}},
            {"hacks": "sometimes"},
            {"enabled": True, "host": ""},
        ):
            with self.subTest(bad=bad), self.assertRaises(self.mod.FtpError):
                self.mod.save(bad)
        self.assertFalse((self.tmp / "ftp.json").exists())

    def test_status_names_the_target_only_when_on(self):
        self.assertEqual(self.mod.status(), {"enabled": False, "name": None})
        self.mod.save({"enabled": True, "host": "10.0.0.5", "preset": "ps5-retroarch"})
        self.assertEqual(self.mod.status(), {"enabled": True, "name": "PS5 (RetroArch)"})
        self.mod.save({"name": "Living room PS5"})
        self.assertEqual(self.mod.status()["name"], "Living room PS5")

    def test_presets_only_hold_valid_settings(self):
        for p in self.mod.iter_presets():
            with self.subTest(preset=p["id"]):
                self.mod._validate({**p["settings"], "host": "example"})


class PathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mod = _load(self.tmp)

    def settings(self, **kw):
        return self.mod._validate({"host": "ps5", "base_path": "/content", **kw})

    def test_platform_folder_mapping_and_hacks_folder(self):
        s = self.settings(folders={"sfam": "snes"})
        self.assertEqual(self.mod.remote_dir(s, "snes", False), "/content/snes")
        self.assertEqual(self.mod.remote_dir(s, "sfam", False), "/content/snes")
        self.assertEqual(self.mod.remote_dir(s, "snes", True), "/content/snes/Hacks")  # "match" follows RomM
        self.assertEqual(self.mod.remote_dir(self.settings(hacks="always"), "nes", False), "/content/nes/Hacks")
        self.assertEqual(self.mod.remote_dir(self.settings(hacks="never"), "nes", True), "/content/nes")
        self.assertEqual(self.mod.remote_dir(self.settings(hacks="always", hacks_folder="Rom Hacks"), "gba", False), "/content/gba/Rom Hacks")
        self.assertEqual(self.mod.remote_dir(self.settings(base_path="/"), "gb", False), "/gb")

    def test_plan_sends_a_file_or_a_folder_game_with_its_layout(self):
        f = self.tmp / "SMW 5 (USA) [RomStop].sfc"
        f.write_bytes(b"x" * 10)
        self.assertEqual(
            [(u.remote, u.size) for u in self.mod.plan(f, "/content/snes")], [("/content/snes/SMW 5 (USA) [RomStop].sfc", 10)]
        )
        game = self.tmp / "Game (Disc set)"
        (game / "sub").mkdir(parents=True)
        (game / "disc1.cue").write_text("a")
        (game / "sub" / "track.bin").write_bytes(b"bb")
        self.assertEqual(
            [u.remote for u in self.mod.plan(game, "/content/psx")],
            ["/content/psx/Game (Disc set)/disc1.cue", "/content/psx/Game (Disc set)/sub/track.bin"],
        )
        with self.assertRaises(self.mod.FtpError):
            self.mod.plan(self.tmp / "missing.sfc", "/content/snes")


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mod = _load(self.tmp)
        self.rom = self.tmp / "Hack (USA) [RomStop].sfc"
        self.rom.write_bytes(os.urandom(600 * 1024))

    def settings(self, **kw):
        return self.mod.merged({"enabled": True, "host": "ps5", "port": 1337, "base_path": "/content", **kw}, {})

    def test_send_creates_folders_and_uploads(self):
        server = FakeFtp()
        s = self.settings()
        uploads = self.mod.plan(self.rom, self.mod.remote_dir(s, "snes", True))
        result = self.mod.send(s, uploads, factory=server)
        self.assertEqual(result, {"sent": 1, "skipped": 0})
        self.assertEqual(server.connected_to, ("ps5", 1337))
        self.assertTrue(server.pasv)
        self.assertEqual(server.files["/content/snes/Hacks/Hack (USA) [RomStop].sfc"], self.rom.read_bytes())
        self.assertTrue(server.closed)

    def test_existing_files_can_be_skipped(self):
        remote = "/content/snes/Hack (USA) [RomStop].sfc"
        server = FakeFtp(files={remote: b"old"})
        s = self.settings(existing="skip")
        result = self.mod.send(s, self.mod.plan(self.rom, "/content/snes"), factory=server)
        self.assertEqual(result, {"sent": 0, "skipped": 1})
        self.assertEqual(server.files[remote], b"old")

    def test_sign_in_uses_the_saved_password(self):
        server = FakeFtp(user="me", password="pw")
        self.mod.send(self.settings(username="me", password="pw"), self.mod.plan(self.rom, "/content/snes"), factory=server)
        self.assertEqual(server.logged_in, "me")
        with self.assertRaises(self.mod.FtpError) as ctx:
            self.mod.send(self.settings(username="me", password="wrong"), [], factory=FakeFtp(user="me", password="pw"))
        self.assertEqual(ctx.exception.code, "login_failed")

    def test_errors_say_what_went_wrong(self):
        with self.assertRaises(self.mod.FtpError) as ctx:
            self.mod.send(self.settings(host="offline"), [], factory=FakeFtp())
        self.assertEqual(ctx.exception.code, "unreachable")
        with self.assertRaises(self.mod.FtpError) as ctx:
            self.mod.send(self.settings(), self.mod.plan(self.rom, "/content/snes"), factory=FakeFtp(fail_stor=True))
        self.assertEqual(ctx.exception.code, "refused")

    def test_test_lists_the_base_path_folders(self):
        server = FakeFtp(dirs=("/content", "/content/snes", "/content/nes", "/content/snes/Hacks"))
        out = self.mod.test(self.settings(), factory=server)
        self.assertEqual(out["folders"], ["nes", "snes"])
        with self.assertRaises(self.mod.FtpError) as ctx:
            self.mod.test(self.settings(base_path="/data"), factory=FakeFtp())
        self.assertEqual(ctx.exception.code, "bad_base_path")

    def test_jobs_record_progress_and_the_result(self):
        server = FakeFtp()
        s = self.settings()
        uploads = self.mod.plan(self.rom, "/content/snes")
        job = self.mod.start(s, uploads, rom_id=7, user_id=1, dest="/content/snes", source="a", factory=server, background=False)
        self.assertEqual(job["status"], "done")
        self.assertEqual((job["done"], job["total"], job["sent"]), (self.rom.stat().st_size, self.rom.stat().st_size, 1))
        self.assertEqual(job["target"], "ps5")
        self.assertIsNone(self.mod.running_for("a"))

        failed = self.mod.start(s, uploads, rom_id=8, user_id=1, dest="/x", factory=FakeFtp(fail_stor=True), background=False)
        self.assertEqual((failed["status"], failed["code"]), ("failed", "refused"))

    def test_a_second_click_returns_the_send_under_way(self):
        s = self.settings()
        uploads = self.mod.plan(self.rom, "/content/snes")
        job_id = self.mod.create_job(s, uploads, rom_id=None, user_id=1, dest="/content/snes", source=str(self.rom))
        again = self.mod.start(s, uploads, rom_id=10, user_id=1, dest="/content/snes", source=str(self.rom), factory=FakeFtp(), background=False)
        self.assertEqual(again["id"], job_id)
        out = self.mod.run_job(job_id, s, uploads, factory=FakeFtp())
        self.assertEqual((out["status"], out["sent"]), ("done", 1))

    def test_a_send_that_cannot_start_is_recorded_with_its_reason(self):
        err = self.mod.FtpError("Send with FTP is off.", "not_configured")
        job_id = self.mod.create_job(None, [], rom_id=11, user_id=1, dest="", status="failed", error=err)
        self.assertEqual((self.mod.job(job_id)["code"], self.mod.job(job_id)["message"]), ("not_configured", "Send with FTP is off."))

    def test_a_job_left_running_by_a_restart_reads_as_failed(self):
        job = self.mod.start(self.settings(), [], rom_id=9, user_id=1, dest="/x", source="b", factory=FakeFtp(), background=False)
        self.mod._update(job["id"], status="running")
        with mock.patch.object(self.mod.time, "time", return_value=job["updated"] + 3600):
            self.assertEqual(self.mod.job(job["id"])["code"], "stalled")
            self.assertIsNone(self.mod.running_for("b"))

    def test_job_files_from_the_first_release_get_the_new_column(self):
        import sqlite3

        con = sqlite3.connect(self.tmp / "ftp.sqlite")
        con.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, rom_id INTEGER, user_id INTEGER, status TEXT, total INTEGER, done INTEGER,"
            " current TEXT, message TEXT, code TEXT, sent INTEGER, skipped INTEGER, target TEXT, dest TEXT, started REAL, updated REAL)"
        )
        con.close()
        job = self.mod.start(self.settings(), [], rom_id=1, user_id=1, dest="/x", source="c", factory=FakeFtp(), background=False)
        self.assertEqual((job["status"], job["source"]), ("done", "c"))


_SEND_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/ftp_send.py"


class SendTests(unittest.TestCase):
    """ftp_send: sending works on the library file, so an install is sent before RomM's scan adds it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.library = self.tmp / "library"
        (self.library / "roms/snes/Hacks").mkdir(parents=True)
        (self.library / "roms/snes/Hacks/Hack [RomStop].sfc").write_bytes(b"rom")
        self.ftp = _load(self.tmp / "resources")
        self.server = FakeFtp(dirs=("/content",))
        library = self.library
        stubs = {
            "config": types.SimpleNamespace(ROMM_AUTH_SECRET_KEY="k"),
            "config.config_manager": types.SimpleNamespace(ROMSTOP_HACKS_FOLDER="Hacks"),
            "handler": types.ModuleType("handler"),
            "handler.filesystem": types.SimpleNamespace(
                fs_rom_handler=types.SimpleNamespace(validate_path=lambda p: library / p),
                fs_asset_handler=types.SimpleNamespace(validate_path=lambda p: library / p),
            ),
            "handler.sync": types.ModuleType("handler.sync"),
            "handler.sync.retroarch": types.ModuleType("handler.sync.retroarch"),
            "handler.sync.retroarch.emulator_names": types.SimpleNamespace(RETROARCH_DIR_BY_ROMM_EMULATOR={"snes9x": "Snes9x"}),
            "logger.logger": types.SimpleNamespace(log=mock.Mock()),
            "handler.romshelf": types.ModuleType("handler.romshelf"),
            "handler.romshelf.ftp": self.ftp,
        }
        stubs["handler.romshelf"].ftp = self.ftp
        with mock.patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location("handler.romshelf.ftp_send", _SEND_PATH)
            self.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.mod)
        # Run transfers inline against the fake server.
        real_start = self.ftp.start
        self.ftp.start = lambda *a, **kw: real_start(*a, **{**kw, "factory": self.server, "background": False})
        real_exists = self.ftp.remote_exists
        self.ftp.remote_exists = lambda s, path: real_exists(s, path, self.server)
        self.record = {"id": "r1", "fs_path": "roms/snes/Hacks", "fs_name": "Hack [RomStop].sfc", "platform_fs_slug": "snes", "rom_id": None}

    def test_an_install_is_sent_before_the_scan(self):
        self.ftp.save({"enabled": True, "host": "ps5", "base_path": "/content"})
        job_id = self.mod.send_install_or_record_failure(self.record, 1)
        job = self.ftp.job(job_id)
        self.assertEqual((job["status"], job["dest"], job["rom_id"]), ("done", "/content/snes/Hacks", None))
        self.assertEqual(self.server.files["/content/snes/Hacks/Hack [RomStop].sfc"], b"rom")

    def test_a_library_game_is_sent_the_same_way(self):
        self.ftp.save({"enabled": True, "host": "ps5", "base_path": "/content", "folders": {"snes": "SNES"}})
        rom = types.SimpleNamespace(id=5, fs_path="roms/snes/Hacks", fs_name="Hack [RomStop].sfc", platform=types.SimpleNamespace(fs_slug="snes"))
        job = self.mod.send_rom(rom, 1)
        self.assertEqual((job["status"], job["rom_id"]), ("done", 5))
        self.assertIn("/content/SNES/Hacks/Hack [RomStop].sfc", self.server.files)

    def test_an_install_still_succeeds_when_the_send_cannot_start(self):
        job = self.ftp.job(self.mod.send_install_or_record_failure(self.record, 1))  # Send with FTP is off
        self.assertEqual((job["status"], job["code"]), ("failed", "not_configured"))
        self.ftp.save({"enabled": True, "host": "ps5"})
        missing = {**self.record, "fs_name": "Gone.sfc"}
        job = self.ftp.job(self.mod.send_install_or_record_failure(missing, 1))
        self.assertEqual((job["status"], job["code"]), ("failed", "missing_file"))
        with self.assertRaises(self.ftp.FtpError):
            self.mod.send_install(missing, 1)  # the Send button shows the reason instead

    # ---- saves and save states

    def _save(self, file_name="Hack [RomStop] [2026-11-04_02-33-57].srm", emulator="snes9x"):
        (self.library / "assets/saves").mkdir(parents=True, exist_ok=True)
        (self.library / "assets/saves" / file_name).write_bytes(b"save-data")
        platform = types.SimpleNamespace(fs_slug="snes", name="Super Nintendo", custom_name=None)
        rom = types.SimpleNamespace(id=5, name="Hack", fs_path="roms/snes/Hacks", fs_name="Hack [RomStop].sfc", fs_name_no_ext="Hack [RomStop]", platform=platform)
        asset = types.SimpleNamespace(file_name=file_name, full_path=f"assets/saves/{file_name}", emulator=emulator)
        return asset, rom

    def test_save_names_follow_the_game_file_the_way_retroarch_looks_for_them(self):
        n = self.ftp.device_save_name
        self.assertEqual(n("Hack [RomStop] [2026-11-04_02-33-57].srm", "Hack [RomStop]"), "Hack [RomStop].srm")
        self.assertEqual(n("Other name.state1", "Hack [RomStop]"), "Hack [RomStop].state1")
        self.assertEqual(n("x.state.auto", "Game"), "Game.state.auto")
        self.assertEqual(n("x.srm", ""), "x.srm")

    def test_save_folders_from_the_settings_and_their_subfolders(self):
        self.ftp.save({"enabled": True, "host": "ps5", "base_path": "/content", "saves_path": "/saves", "states_path": "states/"})
        s = self.ftp.load()
        self.assertEqual((s["saves_path"], s["states_path"]), ("/saves", "/states"))
        self.assertEqual(self.ftp.save_dir(s, "save", "Snes9x", "snes"), "/saves")
        self.assertEqual(self.ftp.save_dir({**s, "saves_layout": "core"}, "state", "Snes9x", "snes"), "/states/Snes9x")
        self.assertEqual(self.ftp.save_dir({**s, "saves_layout": "core_content"}, "save", "Snes9x", "Hacks"), "/saves/Snes9x/Hacks")
        self.assertIsNone(self.ftp.save_dir({**s, "states_path": ""}, "state", None, "snes"))
        with self.assertRaises(self.ftp.FtpError):
            self.ftp.save({"saves_layout": "sideways"})
        with self.assertRaises(self.ftp.FtpError):
            self.ftp.clean_remote_dir("/saves/../etc")

    def test_a_save_goes_to_the_settings_folder_and_never_overwrites_without_asking(self):
        self.ftp.save({"enabled": True, "host": "ps5", "base_path": "/content", "saves_path": "/saves", "saves_layout": "core"})
        asset, rom = self._save()
        plan = self.mod.save_plan("save", asset, rom, 1)
        self.assertEqual((plan["path"], plan["exists"], plan["console"], plan["core_dir"]), ("/saves/Snes9x/Hack [RomStop].srm", False, "Super Nintendo", "Snes9x"))
        job = self.mod.send_save("save", asset, rom, 1, None, None, False)
        self.assertEqual((job["status"], job["dest"]), ("done", "/saves/Snes9x"))
        self.assertEqual(self.server.files["/saves/Snes9x/Hack [RomStop].srm"], b"save-data")
        self.assertEqual(self.ftp.recent_paths(1), [])  # the settings folder isn't a typed-in one

        self.server.files["/saves/Snes9x/Hack [RomStop].srm"] = b"played-on-ps5"
        self.assertTrue(self.mod.save_plan("save", asset, rom, 1)["exists"])
        with self.assertRaises(self.ftp.FtpError) as ctx:
            self.mod.send_save("save", asset, rom, 1, None, None, False)
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("exists", 409))
        self.assertEqual(self.server.files["/saves/Snes9x/Hack [RomStop].srm"], b"played-on-ps5")

        self.mod.send_save("save", asset, rom, 1, None, None, True)
        backups = [v for k, v in self.server.files.items() if ".srm.bak-" in k]
        self.assertEqual(backups, [b"played-on-ps5"])
        self.assertEqual(self.server.files["/saves/Snes9x/Hack [RomStop].srm"], b"save-data")

    def test_a_typed_folder_and_name_are_used_and_the_folder_remembered(self):
        self.ftp.save({"enabled": True, "host": "ps5", "base_path": "/content"})
        asset, rom = self._save("Hack.state1", emulator=None)
        plan = self.mod.save_plan("state", asset, rom, 1)
        self.assertEqual((plan["settings_dir"], plan["path"], plan["core_dir"]), (None, None, None))
        with self.assertRaises(self.ftp.FtpError) as ctx:
            self.mod.send_save("state", asset, rom, 1, None, None, False)
        self.assertEqual(ctx.exception.code, "no_save_folder")

        self.mod.send_save("state", asset, rom, 1, "data/states/", "Mine.state1", False)
        self.mod.send_save("state", asset, rom, 2, "/other", None, False)
        self.assertIn("/data/states/Mine.state1", self.server.files)
        self.assertEqual(self.ftp.recent_paths(1), ["/data/states"])
        self.assertEqual(self.mod.save_plan("state", asset, rom, 1)["recent_paths"], ["/data/states"])
        self.ftp.forget_path(1, "/data/states")
        self.assertEqual(self.ftp.recent_paths(1), [])
        with self.assertRaises(self.ftp.FtpError):
            self.mod.send_save("state", asset, rom, 1, "/data", "../x", False)


try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer
except ImportError:  # optional: pip install pyftpdlib
    DummyAuthorizer = None


@unittest.skipIf(DummyAuthorizer is None, "pyftpdlib is not installed")
class RealServerTests(unittest.TestCase):
    """Against a real FTP server on localhost, with Python's own ftplib client."""

    def setUp(self):
        import logging
        import threading

        logging.getLogger("pyftpdlib").setLevel(logging.WARNING)
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "device"
        (self.root / "content" / "snes").mkdir(parents=True)
        auth = DummyAuthorizer()
        auth.add_user("ps5", "pw", str(self.root), perm="elradfmwMT")
        handler = type("Handler", (FTPHandler,), {"authorizer": auth, "banner": "test"})
        self.server = ThreadedFTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"timeout": 0.2}, daemon=True).start()
        self.mod = _load(self.tmp / "resources")

    def tearDown(self):
        self.server.close_all()

    def test_send_and_test_over_real_ftp(self):
        self.mod.save({"enabled": True, "host": "127.0.0.1", "port": self.port, "username": "ps5", "password": "pw", "base_path": "/content"})
        s = self.mod.load()
        out = self.mod.test(s)
        self.assertEqual(out["folders"], ["snes"])

        game = self.tmp / "SMW 5 (USA) [RomStop].sfc"
        game.write_bytes(os.urandom(700 * 1024))
        uploads = self.mod.plan(game, self.mod.remote_dir(s, "snes", True))
        job = self.mod.start(s, uploads, rom_id=1, user_id=1, dest="/content/snes/Hacks", background=False)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual((self.root / "content/snes/Hacks" / game.name).read_bytes(), game.read_bytes())

        # Sending again replaces the file; with "skip" it is left alone.
        self.assertEqual(self.mod.send(s, uploads), {"sent": 1, "skipped": 0})
        self.mod.save({"existing": "skip"})
        self.assertEqual(self.mod.send(self.mod.load(), uploads), {"sent": 0, "skipped": 1})

        # A save already on the device is renamed to .bak-<time> before the new one is written.
        save = self.tmp / "Game.srm"
        save.write_bytes(b"new")
        (self.root / "saves").mkdir()
        (self.root / "saves/Game.srm").write_bytes(b"old")
        self.assertTrue(self.mod.remote_exists(s, "/saves/Game.srm"))
        self.assertEqual(self.mod.send(s, [self.mod.Upload(save, "/saves/Game.srm", 3, backup=True)]), {"sent": 1, "skipped": 0})
        self.assertEqual((self.root / "saves/Game.srm").read_bytes(), b"new")
        self.assertEqual([p.read_bytes() for p in (self.root / "saves").glob("Game.srm.bak-*")], [b"old"])

        self.mod.save({"password": "wrong"})
        with self.assertRaises(self.mod.FtpError) as ctx:
            self.mod.test(self.mod.load())
        self.assertEqual(ctx.exception.code, "login_failed")


if __name__ == "__main__":
    unittest.main()
