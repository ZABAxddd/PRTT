"""Pruebas de PRTT.  Ejecutar:  python -m unittest discover -s _dev   (desde la carpeta PRTT)"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
sys.path.insert(0, TOOL)

import rpy_parser  # noqa: E402
import sprites  # noqa: E402
from dialogue import build_index, count_lines, search  # noqa: E402
from rpy_parser import parse_position  # noqa: E402
from stage import build_timeline  # noqa: E402


def make_game_dir() -> str:
    """Simula /game con los scripts de muestra y una carpeta PRTT que debe ignorarse."""
    root = tempfile.mkdtemp(prefix="prtt_test_")
    game = os.path.join(root, "game")
    src = os.path.join(HERE, "sample_game")
    for dirpath, _dirs, files in os.walk(src):
        for fn in files:
            if fn.endswith(".rpy.txt"):
                rel = os.path.relpath(os.path.join(dirpath, fn), src)[:-4]   # quita ".txt"
                dst = os.path.join(game, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(os.path.join(dirpath, fn), dst)
    tool = os.path.join(game, "PRTT")
    os.makedirs(tool)
    with open(os.path.join(tool, "no_leer.rpy"), "w", encoding="utf-8") as f:
        f.write('label no_leer:\n    "Esto no debe aparecer"\n')
    return game


class PositionTests(unittest.TestCase):
    def test_all_prefixes(self):
        esperado = {"t11": "appear", "h21": "hop", "f32": "focus", "hf33": "hop_focus",
                    "i41": "instant", "r42": "slide_right", "l43": "slide_left", "s44": "sink"}
        for name, motion in esperado.items():
            p = parse_position(name)
            self.assertIsNotNone(p, name)
            self.assertEqual(p.motion, motion, name)
            self.assertEqual((p.count, p.slot), (int(name[-2]), int(name[-1])), name)

    def test_thide_and_invalid(self):
        self.assertEqual(parse_position("thide").motion, "hide")
        for name in ("t12", "t00", "x11", "t1", "sayori", "t113"):
            self.assertIsNone(parse_position(name), name)


class BaseGame(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = make_game_dir()
        cls.tool = os.path.join(cls.game, "PRTT")
        cls.db = rpy_parser.load(cls.game, exclude_dirs=(cls.tool,))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(os.path.dirname(cls.game), ignore_errors=True)


class ParserTests(BaseGame):
    def test_reads_subfolders_and_skips_tool_dir(self):
        self.assertIn("script-test.rpy", self.db.files)
        self.assertIn("sub/script-extra.rpy", self.db.files)
        self.assertNotIn("PRTT/no_leer.rpy", self.db.files)
        self.assertNotIn("no_leer", self.db.labels)

    def test_python_blocks_skipped(self):
        self.assertTrue(all(e.label != "" or e.kind != "show" for e in self.db.events))

    def test_characters_and_names(self):
        self.assertEqual(self.db.image_tag_of("s"), "sayori")
        self.assertEqual(self.db.display_name("s"), "Sayori")
        self.assertEqual(self.db.display_name("m"), "Monika")
        self.assertEqual(self.db.display_name("y"), "Yuri")

    def test_say_variants(self):
        says = [e for e in self.db.events if e.kind == "say" and e.label == "test_main"]
        self.assertEqual(says[0].data["who"], "s")
        self.assertEqual(says[0].data["attrs"], ["1a"])
        self.assertIn("# esto no es un comentario", says[0].data["text"])
        self.assertEqual(says[3].data["with"], "vpunch")
        narr = next(e for e in says if e.data["who"] == "")
        self.assertEqual(narr.data["plain"], "Narración sin hablante, con estilo.")

    def test_menu_and_string_speaker(self):
        kinds = [e.kind for e in self.db.events if e.label == "test_main"]
        self.assertEqual(kinds.count("choice"), 2)
        e = next(e for e in self.db.events if e.label == "test_stay" and e.kind == "say")
        self.assertEqual(e.data["who"], "Natsuki")


class DialogueTests(BaseGame):
    def test_index_structure(self):
        idx = build_index(self.db)
        self.assertEqual(set(idx), {"script-test.rpy", "sub/script-extra.rpy"})
        names = [i.name for i in idx["script-test.rpy"]]
        self.assertEqual(names, ["test_main", "test_stay", "test_leave"])
        self.assertEqual(count_lines(idx), 10)   # 6 + 1 + 1 en script-test, 2 en la subcarpeta

    def test_lines_have_speaker_names(self):
        idx = build_index(self.db)
        first = idx["script-test.rpy"][0].lines[0]
        self.assertEqual((first.who, first.attrs), ("Sayori", ["1a"]))
        self.assertEqual(first.n, 1)

    def test_search(self):
        idx = build_index(self.db)
        self.assertEqual(len(search(idx, "subcarpeta")), 1)
        self.assertGreaterEqual(len(search(idx, "yuri")), 2)   # por hablante
        self.assertEqual(search(idx, "   "), [])


class StageTests(BaseGame):
    def test_cells_grow_with_characters(self):
        fr = build_timeline(self.db, "test_main")
        self.assertEqual(fr[0].stage.layout_count(), 1)
        self.assertEqual(fr[2].stage.layout_count(), 2)
        self.assertEqual(fr[3].stage.layout_count(), 3)
        cells = fr[2].stage.cells()
        self.assertEqual((cells[0].tag, cells[1].tag), ("monika", "sayori"))

    def test_motion_per_dialogue_and_hide(self):
        fr = build_timeline(self.db, "test_main")
        self.assertEqual(fr[3].stage.actors["monika"].motion, "hop")
        self.assertEqual(fr[3].stage.actors["yuri"].motion, "focus")
        self.assertNotIn("sayori", fr[-1].stage.actors)

    def test_four_cells(self):
        st = build_timeline(self.db, "test_leave")[0].stage
        self.assertEqual(st.layout_count(), 4)
        self.assertEqual([c.tag if c else None for c in st.cells()],
                         ["yuri", "monika", "sayori", None])
        self.assertEqual([c.motion for c in st.cells()[:3]], ["slide_left", "slide_right", "sink"])


def _make_images(game: str) -> None:
    """PNGs de mentira: cada capa es un rectángulo de color con alfa."""
    from PIL import Image
    imgs = os.path.join(game, "images")
    spec = {  # archivo: (tamaño, caja_pintada, color)
        "sayori/1l.png": ((960, 960), (100, 100, 300, 300), (255, 0, 0, 255)),
        "sayori/1r.png": ((960, 960), (500, 100, 700, 300), (0, 255, 0, 255)),
        "sayori/a.png": ((960, 960), (200, 200, 600, 260), (0, 0, 255, 128)),
        "monika/1l.png": ((960, 960), (0, 0, 50, 50), (255, 255, 0, 255)),
        "monika/b.png": ((100, 100), (0, 0, 100, 100), (255, 0, 255, 255)),
        "monika/solo.png": ((200, 300), (0, 0, 200, 300), (9, 9, 9, 255)),
    }
    for rel, (size, box, color) in spec.items():
        path = os.path.join(imgs, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), color), (box[0], box[1]))
        im.save(path)


class SpriteParseTests(unittest.TestCase):
    def test_user_example_exact(self):
        expr = 'im.Composite((960, 960), (0, 0), "sayori/1l.png", (0, 0), "sayori/1r.png", (0, 0), "sayori/a.png")'
        d = sprites.parse_image_expr("sayori 1a", expr)
        self.assertEqual(d.kind, "composite")
        self.assertEqual(d.size, (960, 960))
        self.assertEqual([l.path for l in d.layers], ["sayori/1l.png", "sayori/1r.png", "sayori/a.png"])

    def test_variants(self):
        self.assertEqual(sprites.parse_image_expr("x", '"bg/club.png"').kind, "single")
        self.assertEqual(sprites.parse_image_expr("x", 'Image("a/b.png")').layers[0].path, "a/b.png")
        d = sprites.parse_image_expr("x", 'LiveComposite((10,10),(1,2),"a.png")')
        self.assertEqual((d.kind, d.layers[0].x, d.layers[0].y), ("composite", 1, 2))
        for bad in ("<ATL>", 'im.Flip("a.png", horizontal=True)', "(((", 'im.Composite((1,1),(0,0),im.Scale("a.png",1,1))'):
            self.assertEqual(sprites.parse_image_expr("x", bad).kind, "unsupported", bad)


@unittest.skipUnless(sprites.HAVE_PIL, "Pillow no instalado")
class SpriteComposeTests(BaseGame):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_images(cls.game)
        cls.book = sprites.SpriteBook(cls.db, cls.game)

    def test_find_name_any_order(self):
        self.assertEqual(self.book.find_name("sayori", ["1a"]), "sayori 1a")
        self.assertIsNone(self.book.find_name("sayori", ["9z"]))

    def test_compose_layers_and_alpha(self):
        img, missing = self.book.compose("sayori 1a")
        self.assertEqual(img.size, (960, 960))
        self.assertEqual(missing, [])
        self.assertEqual(img.getpixel((150, 150)), (255, 0, 0, 255))     # capa 1l
        self.assertEqual(img.getpixel((550, 150)), (0, 255, 0, 255))     # capa 1r
        self.assertEqual(img.getpixel((300, 230))[3], 128)               # capa a: semitransparente
        self.assertEqual(img.getpixel((900, 900))[3], 0)                 # fondo transparente

    def test_offset_and_missing_layer(self):
        img, _ = self.book.compose("monika 2b")
        self.assertEqual(img.getpixel((15, 25)), (255, 0, 255, 255))     # capa con offset (10,20)
        img, missing = self.book.compose("sayori 4b")
        self.assertEqual(missing, ["sayori/faltante.png"])               # arma lo que hay y avisa
        self.assertEqual(img.getpixel((150, 150)), (255, 0, 0, 255))

    def test_single_and_errors(self):
        img, _ = self.book.compose("monika 1c")
        self.assertEqual(img.size, (200, 300))
        with self.assertRaises(sprites.SpriteError):
            self.book.compose("yuri 3a")             # su archivo no existe
        with self.assertRaises(sprites.SpriteError):
            self.book.compose("yuri 1b")             # im.Flip: no soportado todavía
        with self.assertRaises(sprites.SpriteError):
            self.book.compose("natsuki 2a")          # ATL

    def test_cell_views_follow_dialogue(self):
        frames = build_timeline(self.db, "test_main")
        v = sprites.build_cell_views(frames[0], self.db, self.book)      # solo Sayori en t11
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].image_name, "sayori 1a")
        self.assertTrue(v[0].speaking)
        self.assertIsNotNone(v[0].image)
        v = sprites.build_cell_views(frames[2], self.db, self.book)      # monika t21 + sayori t22
        self.assertEqual([c.image_name for c in v], ["monika 2b", "sayori 1a"])
        self.assertTrue(v[0].speaking and not v[1].speaking)             # habla Monika
        v = sprites.build_cell_views(frames[3], self.db, self.book)      # 3 celdas: monika, sayori, yuri
        self.assertEqual(len(v), 3)
        self.assertIsNone(v[2].image)                                    # yuri 3a: sin archivo
        self.assertIn("yuri/1.png (FALTA)", v[2].info)

    def test_pose_cli(self):
        import io
        from contextlib import redirect_stdout
        import prtt
        buf = io.StringIO()
        out = os.path.join(os.path.dirname(self.game), "pose.png")
        with redirect_stdout(buf):
            code = prtt.main(["--game", self.game, "--pose", "sayori 1a", "--png", out])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(out))
        self.assertIn("Pose armada: 960x960", buf.getvalue())


class CliTests(BaseGame):
    def test_cli_runs_against_game_dir(self):
        import io
        from contextlib import redirect_stdout
        import prtt
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = prtt.main(["--game", self.game, "--cli", "--label", "test_stay"])
        self.assertEqual(code, 0)
        self.assertIn("Texto con hablante entre comillas.", buf.getvalue())



TCOMMON = """transform tcommon(x=640, z=0.80):
    yanchor 1.0 subpixel True
    on show:
        ypos 1.03
        zoom z*0.95 alpha 0.00
        xcenter x yoffset -20
        easein .25 yoffset 0 zoom z*1.00 alpha 1.00
    on replace:
        alpha 1.00
        parallel:
            easein .25 xcenter x zoom z*1.00
        parallel:
            easein .15 yoffset 0 ypos 1.03
transform focus(x=640, z=0.80):
    yanchor 1.0 ypos 1.03 subpixel True
    on show:
        zoom z*0.95 alpha 0.00
        xcenter x yoffset -20
        easein .25 yoffset 0 zoom z*1.05 alpha 1.00
    on replace:
        alpha 1.00
        parallel:
            easein .25 xcenter x zoom z*1.05
        parallel:
            easein .15 yoffset 0
transform sink(x=640, z=0.80):
    xcenter x yoffset 0 yanchor 1.0 ypos 1.03 zoom z*1.00 alpha 1.00 subpixel True
    easein .5 ypos 1.06
transform t11:
    tcommon(640)
transform t21:
    tcommon(400)
transform t22:
    tcommon(880)
transform f33:
    focus(1040)
transform s22:
    sink(880)
"""

PV_DEFS = """image black = "#000000"
image bg club_day = "bg/club.png"
image bg club_day2:
    choice:
        "bg club_day"
    choice:
        "bg/other.png"
image bg glitch = LiveTile("bg/tile.png")
image end:
    truecenter
    "gui/end.png"
image sayori 1a = im.Composite((960, 960), (0, 0), "sayori/1l.png")
define s = DynamicCharacter('s_name', image='sayori', what_prefix='"', what_suffix='"')
default s_name = "Sayori"
"""

PV_SCRIPT = """label pv:
    scene bg club_day
    show sayori 1a at t11
    s 1a "uno"
    show sayori 1a at t21
    s 1a "dos"
    show sayori 1a at f33
    s 1a "tres"
    show sayori 1a at s22
    s 1a "cuatro"
    scene bg club_day2
    "cinco"
    scene bg glitch
    "seis"
    scene end
    "siete"
    scene black
    "ocho"
"""


def make_preview_game() -> str:
    from PIL import Image
    root = tempfile.mkdtemp(prefix="prtt_pv_")
    game = os.path.join(root, "game")
    os.makedirs(game)
    for name, text in (("definitions.rpy", PV_DEFS), ("transforms.rpy", TCOMMON), ("script.rpy", PV_SCRIPT)):
        with open(os.path.join(game, name), "w", encoding="utf-8") as f:
            f.write(text)

    def save(rel, size, color, box=None):
        path = os.path.join(game, "images", *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        im = Image.new("RGBA", size, color if box is None else (0, 0, 0, 0))
        if box:
            im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), color), (box[0], box[1]))
        im.save(path)

    save("bg/club.png", (1280, 720), (100, 100, 100, 255))
    save("bg/tile.png", (64, 64), (0, 200, 0, 255))
    save("gui/end.png", (200, 100), (200, 0, 200, 255))
    save("gui/textbox.png", (1280, 182), (0, 0, 255, 255))
    save("sayori/1l.png", (960, 960), (255, 0, 0, 255), (100, 100, 300, 300))   # cuadrado rojo
    return game


@unittest.skipUnless(sprites.HAVE_PIL, "Pillow no instalado")
class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import preview
        cls.game = make_preview_game()
        cls.db = rpy_parser.load(cls.game)
        cls.pv = preview.Preview(cls.db, cls.game)
        cls.frames = build_timeline(cls.db, "pv")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(os.path.dirname(cls.game), ignore_errors=True)

    def px(self, i, x, y):
        return self.pv.render(self.frames[i]).getpixel((x, y))

    def test_size_and_background(self):
        img = self.pv.render(self.frames[0])
        self.assertEqual(img.size, (1280, 720))
        self.assertEqual(img.getpixel((1200, 100)), (100, 100, 100))

    def test_positions_from_transforms(self):
        red = (255, 0, 0)
        # t11: x=640, zoom .8 -> cuadrado rojo en x 336..496, y 54..214
        self.assertEqual(self.px(0, 400, 100), red)
        self.assertNotEqual(self.px(0, 150, 100), red)
        # t21: x=400 -> 96..256
        self.assertEqual(self.px(1, 150, 100), red)
        self.assertNotEqual(self.px(1, 400, 100), red)
        # f33: x=1040, zoom .84 -> x 721..889, y 20..188
        self.assertEqual(self.px(2, 800, 100), red)
        # s22: x=880, ypos 1.06 (más abajo) -> y 75..235, x 576..736
        self.assertEqual(self.px(3, 650, 150), red)
        self.assertNotEqual(self.px(3, 650, 60), red)

    def test_textbox_in_front_of_sprite(self):
        # la caja azul tapa lo que haya debajo
        self.assertEqual(self.px(0, 30, 700), (0, 0, 255))

    def test_backgrounds(self):
        self.assertEqual(self.px(4, 1200, 100), (100, 100, 100))      # ATL choice -> bg club_day
        self.assertEqual(self.px(5, 1000, 300), (0, 200, 0))          # LiveTile rellena la pantalla
        self.assertEqual(self.px(6, 640, 360), (200, 0, 200))         # truecenter (200x100 centrada: y 310..410)
        self.assertEqual(self.px(6, 10, 10), (0, 0, 0))
        self.assertEqual(self.px(7, 640, 300), (0, 0, 0))             # color "#000000"

    def test_dialogue_text_adds_quotes(self):
        self.assertEqual(self.pv.dialogue_text("uno", "s"), '"uno"')
        self.assertEqual(self.pv.dialogue_text("narra", ""), "narra")
        self.assertEqual(self.pv.speaker_label("s"), "Sayori")

    def test_cell_rects(self):
        self.assertEqual(self.pv.cell_rects(0), [])
        # t21/t22 están en tu transforms (x=400, 880); t3x y t4x usan los valores estándar de respaldo
        xs = lambda n: [round((r[2] + r[4]) / 2) for r in self.pv.cell_rects(n)]
        self.assertEqual(xs(1), [640])
        self.assertEqual(xs(2), [400, 880])
        self.assertEqual(xs(3), [240, 640, 1040])
        self.assertEqual(xs(4), [200, 493, 786, 1080])
        self.assertEqual([r[1] for r in self.pv.cell_rects(3)], ["t31", "t32", "t33"])
        for r in self.pv.cell_rects(4):
            self.assertTrue(0 <= r[2] < r[4] <= 1280 and 0 <= r[3] < r[5] <= 720)

    def test_cli_preview(self):
        import io
        from contextlib import redirect_stdout
        import prtt
        out = os.path.join(os.path.dirname(self.game), "pv.png")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = prtt.main(["--game", self.game, "--preview", "pv", "--n", "1", "--png", out])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(prtt.main(["--game", self.game, "--preview", "pv", "--n", "99"]), 1)


if __name__ == "__main__":
    unittest.main()
