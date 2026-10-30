"""test_rpyc.py - pruebas de rpyc_reader / rpyc_decompile / carga de .rpyc en ScriptDB.

Los .rpyc se fabrican aquí mismo: se crean clases con el nombre de las de Ren'Py (renpy.ast.Say...) solo mientras se
hace el pickle. NOTA: los campos de los nodos están escritos según lo que se sabe de Ren'Py; la prueba contra un
.rpyc REAL es test_golden_real (se salta si no encuentra script-ch0.rpy/.rpyc junto a este archivo o en PRTT_RPYC_FIXTURES).
"""
import os
import pickle
import struct
import sys
import tempfile
import types
import unittest
import zlib

import rpa_reader
import rpy_parser
import rpyc_decompile
import rpyc_reader
from test_rpa_reader import make_rpa


# ---------------------------------------------------------------------- fabricar .rpyc
class _Factory:
    """Crea objetos cuyo pickle dice «renpy.ast.<Nombre>»."""

    def __init__(self):
        self.classes = {}

    def __call__(self, _module, _kind, **attrs):
        module, name = _module, _kind
        key = (module, name)
        if key not in self.classes:
            cls = type(name, (), {"__module__": module})
            self.classes[key] = cls
        obj = self.classes[key].__new__(self.classes[key])
        obj.__dict__.update(attrs)
        return obj

    def dumps(self, obj):
        mods = {}
        for (module, name), cls in self.classes.items():
            parts = module.split(".")
            for i in range(1, len(parts) + 1):
                mods.setdefault(".".join(parts[:i]), types.ModuleType(".".join(parts[:i])))
            setattr(mods[module], name, cls)
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        try:
            return pickle.dumps(obj, protocol=2)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


F = _Factory()


def ast(_kind, **kw):
    kw.setdefault("filename", "game/x.rpy")
    return F("renpy.ast", _kind, **kw)


def atl(_kind, **kw):
    return F("renpy.atl", _kind, **kw)


def pycode(src):
    code = F("renpy.ast", "PyCode")
    code.source, code.location, code.mode = src, ("game/x.rpy", 1), "exec"
    return code


def make_rpyc(stmts, rpc1=False):
    blob = zlib.compress(F.dumps(({"version": 5003000, "key": "unlocked"}, stmts)))
    if rpc1:
        return blob
    head = b"RENPY RPC2"
    table_len = 12 * 2
    return head + struct.pack("<III", 1, len(head) + table_len, len(blob)) + struct.pack("<III", 0, 0, 0) + blob


def spec(*name, at=(), zorder=None, tag=None):
    return (tuple(name), None, tag, list(at), None, zorder, [])


class DecompileTests(unittest.TestCase):
    def dec(self, stmts):
        f = rpyc_reader.load_rpyc(make_rpyc(stmts))
        return rpyc_decompile.decompile(f.stmts)

    def test_say_show_scene_with_and_lines(self):
        stmts = [ast("Label", name="start", linenumber=1, parameters=None, hide=False, block=[
            ast("Scene", imspec=spec("bg", "club"), layer=None, atl=None, linenumber=2),
            ast("With", expr="dissolve", paired=None, linenumber=3),
            ast("Show", imspec=spec("sayori", "1a", at=["t11"], zorder="2"), atl=None, linenumber=5),
            ast("Say", who="s", what='Hola "mundo"\nlínea', interact=True, attributes=("1b",), with_=None, linenumber=6),
            ast("Say", who=None, what="Narrador", interact=True, attributes=None, with_=None, linenumber=7),
            ast("Hide", imspec=spec("sayori"), linenumber=8),
            ast("UserStatement", line="play music t2", linenumber=9, block=[]),
        ])]
        text = self.dec(stmts).text.splitlines()
        self.assertEqual(text[0], "label start:")
        self.assertEqual(text[1], "    scene bg club")
        self.assertEqual(text[2], "    with dissolve")
        self.assertEqual(text[3], "")                                         # línea 4 vacía: se conservan las líneas
        self.assertEqual(text[4], "    show sayori 1a at t11 zorder 2")
        self.assertEqual(text[5], '    s 1b "Hola \\"mundo\\"\\nlínea"')
        self.assertEqual(text[6], '    "Narrador"')
        self.assertEqual(text[8], "    play music t2")

    def test_paired_with(self):
        stmts = [ast("Label", name="a", linenumber=1, parameters=None, hide=False, block=[
            ast("With", expr="None", paired="dissolve", linenumber=2),
            ast("Show", imspec=spec("m", "1a"), atl=None, linenumber=2),
            ast("With", expr="dissolve", paired="dissolve", linenumber=2),
        ])]
        text = self.dec(stmts).text
        self.assertIn("show m 1a with dissolve", text)
        self.assertEqual(text.count("with"), 1)

    def test_menu_if_jump_call(self):
        stmts = [ast("Label", name="m", linenumber=1, parameters=None, hide=False, block=[
            ast("Say", who="s", what="¿Qué hacemos?", interact=False, attributes=None, with_=None, linenumber=3),
            ast("Menu", linenumber=2, set=None, with_=None, items=[
                ("Ir", "True", [ast("Jump", target="fin", expression=False, linenumber=5)]),
                ("Quedarse", "x > 1", [ast("Pass", linenumber=7)]),
                ("Caption", None, None)]),
            ast("If", linenumber=9, entries=[("a", [ast("Pass", linenumber=10)]), ("b", [ast("Pass", linenumber=12)]),
                                             ("True", [ast("Return", expression=None, linenumber=14)])]),
            ast("Call", label="otro", expression=False, arguments=None, linenumber=15),
            ast("Label", name="_call_otro_1", linenumber=15, parameters=None, hide=False, block=[]),
            ast("Return", expression=None, linenumber=16),
        ])]
        text = self.dec(stmts).text
        for expect in ('    menu:', '        s "¿Qué hacemos?"', '        "Ir":', '            jump fin',
                       '        "Quedarse" if x > 1:', '    if a:', '    elif b:', '    else:', '    call otro'):
            self.assertIn(expect, text, expect)
        self.assertNotIn("_call_otro_1", text)
        db = rpy_parser.ScriptDB()
        db.parse_text(text, "x.rpy")
        kinds = [e.kind for e in db.events]
        for k in ("label", "menu", "choice", "jump", "flow", "call", "return"):
            self.assertIn(k, kinds)

    def test_named_menu_and_defs(self):
        stmts = [
            ast("Init", priority=0, linenumber=1, block=[ast("Define", varname="s", store="store", linenumber=1,
                code=pycode('Character("Sayori", image="sayori")'))]),
            ast("Init", priority=0, linenumber=2, block=[ast("Define", varname="text_size", store="store.gui", linenumber=2,
                code=pycode("22"))]),
            ast("Init", priority=500, linenumber=3, block=[ast("Image", imgname=("bg", "club"), linenumber=3, atl=None,
                code=pycode('"bg/club.png"'))]),
            ast("Init", priority=0, linenumber=4, block=[ast("Transform", varname="t11", linenumber=4, parameters=None,
                atl=atl("RawBlock", animation=False, statements=[
                    atl("RawMultipurpose", warper=None, warp_function=None, duration="0", expressions=[],
                        properties=[("xcenter", "640"), ("yoffset", "0")], splines=[], revolution=None, circles="0"),
                    atl("RawMultipurpose", warper="linear", warp_function=None, duration="0.5", expressions=[],
                        properties=[("alpha", "0.0")], splines=[], revolution=None, circles="0"),
                    atl("RawParallel", blocks=[atl("RawBlock", animation=False, statements=[
                        atl("RawMultipurpose", warper="pause", warp_function=None, duration="1.0", expressions=[],
                            properties=[], splines=[], revolution=None, circles="0")])]),
                    atl("RawRepeat", repeats=None)]))]),
            ast("Init", priority=0, linenumber=8, block=[ast("Python", store="store", hide=False, linenumber=8,
                code=pycode("\ndef f():\n    return 1\n"))]),
            ast("Init", priority=0, linenumber=12, block=[ast("Style", style_name="say_dialogue", parent="default",
                properties={"xpos": "0.5"}, linenumber=12)]),
            ast("Label", name="m2", linenumber=14, parameters=None, hide=False, block=[]),
            ast("Menu", linenumber=14, set=None, with_=None, items=[("Sí", "True", [ast("Pass", linenumber=15)])]),
        ]
        res = self.dec(stmts)
        text = res.text
        self.assertEqual(res.warnings, [])
        for expect in ('define s = Character("Sayori", image="sayori")', "define gui.text_size = 22",
                       'image bg club = "bg/club.png"', "transform t11:", "        xcenter 640 yoffset 0".strip(),
                       "linear 0.5 alpha 0.0", "parallel:", "pause 1.0", "repeat", "init python:", "    def f():",
                       "style say_dialogue is default:", "xpos 0.5", "menu m2:"):
            self.assertIn(expect, text, expect)
        db = rpy_parser.ScriptDB()
        db.parse_text(text, "x.rpy")
        self.assertIn("s", db.characters)
        self.assertEqual(db.images["bg club"], '"bg/club.png"')
        self.assertIn("t11", db.transform_defs)
        self.assertIn("say_dialogue", db.styles)
        self.assertEqual([e.data["name"] for e in db.events if e.kind == "menu"], ["m2"])

    def test_unknown_node_warns_once_and_continues(self):
        stmts = [ast("Label", name="a", linenumber=1, parameters=None, hide=False, block=[
            ast("Mystery", linenumber=2), ast("Mystery", linenumber=3),
            ast("Say", who=None, what="sigue", interact=True, attributes=None, with_=None, linenumber=4)])]
        res = self.dec(stmts)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn('"sigue"', res.text)

    def test_screen_and_translate_ignored(self):
        stmts = [ast("Init", priority=0, linenumber=1, block=[ast("Screen", linenumber=1)]),
                 ast("Label", name="a", linenumber=2, parameters=None, hide=False, block=[
                     ast("Translate", language="spanish", identifier="x", linenumber=3,
                         block=[ast("Say", who=None, what="OTRO IDIOMA", interact=True, attributes=None, with_=None, linenumber=3)])])]
        res = self.dec(stmts)
        self.assertEqual(res.warnings, [])
        self.assertNotIn("OTRO IDIOMA", res.text)

    def test_implicit_return_dropped_only_after_labelled_block(self):
        lab = ast("Label", name="a", linenumber=1, parameters=None, hide=False,
                  block=[ast("Return", expression=None, linenumber=2)])
        self.assertEqual(self.dec([lab, ast("Return", expression=None, linenumber=2)]).text.count("return"), 1)
        flat = [ast("Label", name="a", linenumber=1, parameters=None, hide=False, block=[]),
                ast("Return", expression=None, linenumber=2)]
        self.assertEqual(self.dec(flat).text.count("return"), 1)


class ReaderTests(unittest.TestCase):
    def test_rpc1_and_rpc2(self):
        stmts = [ast("Pass", linenumber=1)]
        for rpc1 in (False, True):
            f = rpyc_reader.load_rpyc(make_rpyc(stmts, rpc1=rpc1))
            self.assertEqual(f.format, "RPC1" if rpc1 else "RPC2")
            self.assertEqual(type(f.stmts[0]).__name__, "Pass")

    def test_malicious_pickle_rejected(self):
        class Evil:
            def __reduce__(self):
                return (os.system, ("echo pwned",))
        blob = zlib.compress(pickle.dumps(({}, [Evil()]), protocol=2))
        with self.assertRaises(rpyc_reader.RpycError):
            rpyc_reader.load_rpyc(blob)

    def test_garbage_and_truncated(self):
        for data in (b"", b"no es un rpyc", b"RENPY RPC2", make_rpyc([ast("Pass", linenumber=1)])[:30]):
            with self.assertRaises(rpyc_reader.RpycError):
                rpyc_reader.load_rpyc(data)

    def test_utf8_from_python2_str_is_repaired(self):
        self.assertEqual(rpyc_reader.fix_text("cafÃ©"), "café")
        self.assertEqual(rpyc_reader.fix_text("café"), "café")
        self.assertEqual(rpyc_reader.fix_text("plain"), "plain")


class FutureProofTests(unittest.TestCase):
    def test_default_language_translate_keeps_dialogue(self):
        say = ast("Say", who="s", what="hola", interact=True, attributes=None, with_=None, linenumber=3)
        stmts = [ast("Label", name="a", linenumber=2, parameters=None, hide=False, block=[
            ast("Translate", language=None, identifier="a_1", linenumber=3, block=[say])])]
        text = rpyc_decompile.decompile(rpyc_reader.load_rpyc(make_rpyc(stmts)).stmts).text
        self.assertIn('s "hola"', text)

    def test_dead_position_transform_is_dropped(self):
        db = rpy_parser.ScriptDB()
        db.parse_text("transform t11:\n    tcommon(640)\n\ntransform t21:\n    xcenter 320\n", "t.rpy")
        db._drop_dead_positions()
        self.assertEqual(list(db.transform_defs), ["t21"])

    def test_params_in_any_shape(self):
        for params in ([("x", "640"), ("z", "0.80")], {"x": "640", "z": "0.80"}, [("x", "640", 1), ("z", "0.80", 1)]):
            info = F("renpy.ast", "ParameterInfo", parameters=params, extrapos=None, extrakw=None)
            self.assertEqual(rpyc_decompile.fmt_params(info), "(x=640, z=0.80)")

    def test_pyexpr_with_extra_args_ren_py_8_5(self):
        e = rpyc_reader.PyExpr("x + 1", "game/a.rpy", 3, None, "hash", 4, "extra")
        self.assertEqual(e, "x + 1")
        self.assertEqual(rpyc_reader.PyExpr("y"), "y")

    def test_unknown_future_classes_become_inert_stubs(self):
        extra = F("renpy.nueva.cosa", "NodoDelFuturo", valor=1)
        stmts = [ast("Label", name="a", linenumber=1, parameters=None, hide=False, block=[
            ast("Say", who=None, what="sigue", interact=True, attributes=None, with_=None, linenumber=2, extra=extra)])]
        f = rpyc_reader.load_rpyc(make_rpyc(stmts))
        self.assertIn('"sigue"', rpyc_decompile.decompile(f.stmts).text)

    def test_bad_node_does_not_kill_file(self):
        stmts = [ast("Label", name="a", linenumber=1, parameters=None, hide=False, block=[
            ast("Jump", expression=False, linenumber=2),                      # sin `target`: falla solo ese nodo
            ast("Say", who=None, what="sigue", interact=True, attributes=None, with_=None, linenumber=3)])]
        res = rpyc_decompile.decompile(rpyc_reader.load_rpyc(make_rpyc(stmts)).stmts)
        self.assertIn('"sigue"', res.text)
        self.assertEqual(len(res.warnings), 1)

    def test_definitions_inside_rpa_feed_the_db(self):
        defs = [
            ast("Init", priority=0, linenumber=1, block=[ast("Define", varname="mi", store="store", linenumber=1,
                code=pycode('Character("Mita", image="mita")'))]),
            ast("Init", priority=500, linenumber=2, block=[ast("Image", imgname=("mita", "1a"), linenumber=2, atl=None,
                code=pycode('"mita/1a.png"'))]),
            ast("Init", priority=0, linenumber=3, block=[ast("Define", varname="t1", store="store.audio", linenumber=3,
                code=pycode('"bgm/1.ogg"'))]),
        ]
        game = tempfile.mkdtemp()
        make_rpa(os.path.join(game, "scripts.rpa"), {"definitions.rpyc": make_rpyc(defs)})
        db = rpy_parser.load(game)
        self.assertIn("mi", db.characters)
        self.assertIn("mita 1a", db.images)
        self.assertIn("audio.t1", db.defines)

    def test_writer_refuses_readonly(self):
        try:
            import rpy_writer
        except ImportError as exc:
            self.skipTest(str(exc))
        w = rpy_writer.ScriptWriter(tempfile.mkdtemp(), backup=False)
        w.readonly = {"x.rpy"}
        with self.assertRaises(ValueError):
            w.read_statements("x.rpy", [1])
        r = w.apply_dialogues([rpy_writer.DialogueEdit("k", "x.rpy", 1, "a", "b")])
        self.assertFalse(r[0].ok)
        self.assertIn("no se puede modificar", r[0].msg)


class DbIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = self.tmp.name
        stmts = [ast("Label", name="from_rpyc", linenumber=2, parameters=None, hide=False, block=[
            ast("Say", who=None, what="desde rpyc", interact=True, attributes=None, with_=None, linenumber=3)])]
        self.rpyc = make_rpyc(stmts)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data, mode="wb"):
        p = os.path.join(self.game, *name.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode) as f:
            f.write(data)

    def test_loose_rpyc_is_read_only_and_keeps_line(self):
        self.write("script-x.rpyc", self.rpyc)
        db = rpy_parser.load(self.game)
        self.assertEqual(db.files, ["script-x.rpy"])
        self.assertEqual(db.readonly, {"script-x.rpy"})
        ev = [e for e in db.events if e.kind == "say"][0]
        self.assertEqual((ev.file, ev.line, ev.label), ("script-x.rpy", 3, "from_rpyc"))
        self.assertIn("from_rpyc", db.labels)
        self.assertIn("script-x.rpy", db.sources)

    def test_loose_rpy_wins_over_rpyc(self):
        self.write("script-x.rpyc", self.rpyc)
        self.write("script-x.rpy", 'label from_rpy:\n    "desde rpy"\n', "w")
        db = rpy_parser.load(self.game)
        self.assertEqual(db.files, ["script-x.rpy"])
        self.assertEqual(db.readonly, set())
        self.assertIn("from_rpy", db.labels)
        self.assertNotIn("from_rpyc", db.labels)

    def test_rpyc_inside_rpa(self):
        make_rpa(os.path.join(self.game, "scripts.rpa"), {"sub/script-y.rpyc": self.rpyc, "x.png": b"PNG"})
        db = rpy_parser.load(self.game)
        self.assertEqual(db.files, ["sub/script-y.rpy"])
        self.assertTrue(db.compiled_from["sub/script-y.rpy"].startswith("rpa://"))
        self.assertIn("from_rpyc", db.labels)

    def test_broken_rpyc_goes_to_errors_and_rest_loads(self):
        self.write("bad.rpyc", b"basura")
        self.write("good.rpy", 'label ok:\n    "hola"\n', "w")
        db = rpy_parser.load(self.game)
        self.assertEqual(db.files, ["good.rpy"])
        self.assertTrue(any("bad.rpyc" in e for e in db.errors))

    def test_audio_reads_decompiled_source(self):
        import audio
        stmts = [ast("Init", priority=0, linenumber=1, block=[ast("Define", varname="t1", store="store.audio", linenumber=1,
                code=pycode('"<loop 1.0>bgm/1.ogg"'))])]
        self.write("definitions.rpyc", make_rpyc(stmts))
        db = rpy_parser.load(self.game)
        tracks = audio.collect_tracks(db, self.game)
        self.assertEqual([t.var for t in tracks], ["audio.t1"])


class GoldenRealTests(unittest.TestCase):
    """Compara los eventos del .rpy real con los del .rpyc real descompilado (se salta si no están)."""

    def test_golden_real(self):
        base = os.environ.get("PRTT_RPYC_FIXTURES") or os.path.dirname(os.path.abspath(__file__))
        rpy, rpyc = os.path.join(base, "script-ch0.rpy"), os.path.join(base, "script-ch0.rpyc")
        if not (os.path.isfile(rpy) and os.path.isfile(rpyc)):
            self.skipTest("sin script-ch0.rpy/.rpyc")
        res = rpyc_decompile.decompile(rpyc_reader.read_rpyc(rpyc).stmts)
        self.assertEqual(res.warnings, [])
        a, b = rpy_parser.ScriptDB(), rpy_parser.ScriptDB()
        with open(rpy, encoding="utf-8-sig") as f:
            a.parse_text(f.read(), "script-ch0.rpy")
        b.parse_text(res.text, "script-ch0.rpy")
        self.assertEqual(len(a.events), len(b.events))
        diff = [(x.line, x.raw) for x, y in zip(a.events, b.events)
                if (x.kind, x.line, x.label, x.indent, x.data) != (y.kind, y.line, y.label, y.indent, y.data)]
        # el .rpyc de ejemplo es de otra compilación: llama a restore_all_characters() en vez de restore_characters()
        self.assertLessEqual(len(diff), 1, diff)


if __name__ == "__main__":
    unittest.main()
