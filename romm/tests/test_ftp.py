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
        job = self.mod.start(s, uploads, rom_id=7, user_id=1, dest="/content/snes", factory=server, background=False)
        self.assertEqual(job["status"], "done")
        self.assertEqual((job["done"], job["total"], job["sent"]), (self.rom.stat().st_size, self.rom.stat().st_size, 1))
        self.assertEqual(job["target"], "ps5")
        self.assertIsNone(self.mod.running_for(7))

        failed = self.mod.start(s, uploads, rom_id=8, user_id=1, dest="/x", factory=FakeFtp(fail_stor=True), background=False)
        self.assertEqual((failed["status"], failed["code"]), ("failed", "refused"))

    def test_a_job_left_running_by_a_restart_reads_as_failed(self):
        job = self.mod.start(self.settings(), [], rom_id=9, user_id=1, dest="/x", factory=FakeFtp(), background=False)
        self.mod._update(job["id"], status="running")
        with mock.patch.object(self.mod.time, "time", return_value=job["updated"] + 3600):
            self.assertEqual(self.mod.job(job["id"])["code"], "stalled")
            self.assertIsNone(self.mod.running_for(9))


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

        self.mod.save({"password": "wrong"})
        with self.assertRaises(self.mod.FtpError) as ctx:
            self.mod.test(self.mod.load())
        self.assertEqual(ctx.exception.code, "login_failed")


if __name__ == "__main__":
    unittest.main()
