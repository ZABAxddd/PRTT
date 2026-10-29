"""test_rpa_reader.py - pruebas de rpa_reader con .rpa sintéticos (sin archivos del juego)."""
import io
import os
import pickle
import tempfile
import unittest
import zlib

import rpa_reader as rr


def make_rpa(path, files, key=0x1234ABCD, version="RPA-3.0", prefix_for=None, protocol=2):
    """Escribe un .rpa como lo hace Ren'Py. files: {nombre: bytes}; prefix_for: {nombre: prefijo guardado en el índice}."""
    prefix_for = prefix_for or {}
    k = 0 if version == "RPA-2.0" else key
    header_len = 64
    with open(path, "wb") as f:
        f.write(b"\0" * header_len)
        index = {}
        for name, data in files.items():
            pre = prefix_for.get(name, b"")
            off = f.tell()
            f.write(data)
            seg = (off ^ k, (len(data) + len(pre)) ^ k)
            index[name] = [seg + (pre,)] if name in prefix_for else [seg]
        idx_off = f.tell()
        f.write(zlib.compress(pickle.dumps(index, protocol=protocol)))
        header = (f"{version} {idx_off:016x}" + ("" if version == "RPA-2.0" else f" {key:08x}") + "\n").encode()
        assert len(header) < header_len
        f.seek(0)
        f.write(header)


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


class RpaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = self.tmp.name
        self.files = {"images/sayori/1l.png": b"PNGDATA-L", "bgm/1.ogg": b"OggS-fake", "images/bg/Club Day.png": b"BG"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_rpa3_roundtrip(self):
        make_rpa(os.path.join(self.game, "images.rpa"), self.files)
        v = rr.RpaVault(self.game)
        self.assertTrue(v)
        for n, d in self.files.items():
            self.assertEqual(v.read(n), d)

    def test_rpa2(self):
        make_rpa(os.path.join(self.game, "old.rpa"), {"a.txt": b"hola"}, version="RPA-2.0")
        self.assertEqual(rr.RpaVault(self.game).read("a.txt"), b"hola")

    def test_prefix_is_prepended(self):
        make_rpa(os.path.join(self.game, "a.rpa"), {"x.bin": b"BODY"}, prefix_for={"x.bin": b"HEAD"})
        self.assertEqual(rr.RpaVault(self.game).read("x.bin"), b"HEADBODY")

    def test_prefix_search_and_case(self):
        make_rpa(os.path.join(self.game, "images.rpa"), self.files)
        v = rr.RpaVault(self.game)
        self.assertEqual(v.find("sayori/1l.png", ("images", "")), "images/sayori/1l.png")
        self.assertEqual(v.find("bg/club day.png", ("images",)), "images/bg/Club Day.png")
        self.assertIsNone(v.find("nope.png", ("images", "")))

    def test_asset_source_is_bytesio(self):
        make_rpa(os.path.join(self.game, "images.rpa"), self.files)
        ref = rr.find_in_archives(self.game, "sayori/1l.png", ("images", ""))
        self.assertTrue(rr.is_virtual(ref))
        self.assertTrue(rr.exists(ref))
        src = rr.asset_source(ref)
        self.assertIsInstance(src, io.BytesIO)
        self.assertEqual(src.read(), b"PNGDATA-L")
        self.assertEqual(rr.asset_source("C:/real/path.png"), "C:/real/path.png")

    def test_real_path_extracts_once(self):
        make_rpa(os.path.join(self.game, "audio.rpa"), self.files)
        ref = rr.find_in_archives(self.game, "bgm/1.ogg", ("",))
        p = rr.real_path(ref)
        self.assertEqual(read_file(p), b"OggS-fake")
        self.assertEqual(rr.real_path(ref), p)
        self.assertEqual(rr.real_path("C:/x.ogg"), "C:/x.ogg")

    def test_py3_pickles_with_bytes_load(self):
        # protocolo 2 de Python 3: bytes() vacío -> builtins.bytes, no vacío -> _codecs.encode
        make_rpa(os.path.join(self.game, "r8.rpa"), {"k.txt": b"abc"}, prefix_for={"k.txt": b""})
        make_rpa(os.path.join(self.game, "r9.rpa"), {"m.txt": b"xyz"}, prefix_for={"m.txt": b"\xe9"})
        v = rr.RpaVault(self.game)
        self.assertEqual(v.read("k.txt"), b"abc")
        self.assertEqual(v.read("m.txt"), b"\xe9xyz")

    def test_malicious_pickle_rejected(self):
        class Evil:
            def __reduce__(self):
                return (os.system, ("echo pwned",))
        p = os.path.join(self.game, "evil.rpa")
        with open(p, "wb") as f:
            f.write(b" " * 64)
            off = f.tell()
            f.write(zlib.compress(pickle.dumps({"a": Evil()}, protocol=2)))
            f.seek(0)
            f.write(f"RPA-3.0 {off:016x} 00000000\n".encode())
        v = rr.RpaVault(self.game)
        self.assertFalse(v)
        self.assertEqual(len(v.errors), 1)

    def test_zip_slip_blocked(self):
        make_rpa(os.path.join(self.game, "z.rpa"), {"../../evil.txt": b"x"})
        with self.assertRaises(rr.RpaError):
            rr.RpaVault(self.game).extract("../../evil.txt")

    def test_garbage_rpa_does_not_break_scan(self):
        with open(os.path.join(self.game, "bad.rpa"), "wb") as f:
            f.write(b"not an rpa at all\n")
        make_rpa(os.path.join(self.game, "good.rpa"), self.files)
        v = rr.RpaVault(self.game)
        self.assertEqual(len(v.archives), 1)
        self.assertEqual(len(v.errors), 1)

    def test_new_rpa_is_picked_up_without_restart(self):
        self.assertIsNone(rr.find_in_archives(self.game, "bgm/1.ogg", ("",)))
        make_rpa(os.path.join(self.game, "audio.rpa"), self.files)
        self.assertEqual(rr.find_in_archives(self.game, "bgm/1.ogg", ("",)), "rpa://bgm/1.ogg")

    def test_parallel_extraction_same_file(self):
        import threading
        make_rpa(os.path.join(self.game, "audio.rpa"), self.files)
        ref = rr.find_in_archives(self.game, "bgm/1.ogg", ("",))
        out, errs = [], []

        def work():
            try:
                out.append(rr.real_path(ref))
            except Exception as exc:                 # pragma: no cover
                errs.append(exc)
        ts = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(len(set(out)), 1)
        self.assertEqual(read_file(out[0]), b"OggS-fake")

    def test_no_rpa_is_falsy_and_returns_none(self):
        self.assertIsNone(rr.find_in_archives(self.game, "a.png", ("images", "")))


if __name__ == "__main__":
    unittest.main()
