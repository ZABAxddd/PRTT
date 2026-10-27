"""Casos reales de DDLC que los demás tests no cubren: `with` tras los show, fondos Solid, capas (dark...) al
agregar un scene, aviso de scene duplicado. Usa archivos .rpy reales."""
import os
import tempfile

import rpy_parser
import scene_edit as se
import scenes
from scene_ctl import SceneController
from stage import build_timeline

DEFS = ('image bg club_day = "#112233"\nimage bg class_day = "#223344"\n'
        'image bg blanco = Solid("#ffffff")\nimage bg gris = Solid((10, 20, 30, 255))\n'
        'image bg corto = Solid("#abc")\nimage bg alfa = "#12345678"\nimage bg raro = Solid(color_var)\n'
        'image dark = "#000000"\n')
SCRIPT = ('define s = Character("Sayori", image="sayori")\n'            # 1
          'define m = Character("Monika", image="monika")\n'             # 2
          'label ch1_main:\n'                                            # 3
          '    scene bg club_day\n'                                      # 4
          '    show sayori 1a zorder 2 at t11\n'                         # 5
          '    show dark zorder 5\n'                                     # 6
          '    with dissolve_scene_full\n'                               # 7
          '    s "Uno"\n'                                                # 8
          '    s "Dos"\n'                                                # 9
          '    scene bg class_day\n'                                     # 10
          '    show monika 1a at t11\n'                                  # 11
          '    with wipeleft\n'                                          # 12
          '    m "Tres"\n')                                              # 13


def setup(tmp, script=SCRIPT):
    open(os.path.join(tmp, "definitions.rpy"), "w", encoding="utf-8").write(DEFS)
    open(os.path.join(tmp, "script.rpy"), "w", encoding="utf-8").write(script)
    db = rpy_parser.load(tmp)
    return db, build_timeline(db, "ch1_main")


def rd(tmp, n="script.rpy"):
    return open(os.path.join(tmp, n), encoding="utf-8").read()


def mt(tmp):
    return {f: os.stat(os.path.join(tmp, f)).st_mtime_ns for f in os.listdir(tmp) if f.endswith(".rpy")}


def bgs(db):
    return {b.name: b for b in scenes.collect_backgrounds(db)}


with tempfile.TemporaryDirectory() as tmp:
    db, fr = setup(tmp)

    # --- 1. el `with` que cierra el bloque (tras los show) es la transición de la escena
    cur, prev = se.find_scenes(db, fr[0])
    assert (cur.transition, cur.next_with_line) == ("dissolve_scene_full", 7) and cur.inline_with is None
    cur, prev = se.find_scenes(db, fr[2])
    assert (cur.name, cur.transition, cur.next_with_line) == ("bg class_day", "wipeleft", 12)
    assert (prev.name, prev.transition) == ("bg club_day", "dissolve_scene_full")
    # ...pero no se confunde con otros `with`
    for script, want, line in (
        ('label ch1_main:\n    scene bg a with dissolve\n    show s 1a at t11\n    with moveinleft\n    "x"\n', "dissolve", 0),
        ('label ch1_main:\n    scene bg a\n    "x"\n    with dissolve\n    "y"\n', None, 0),                    # tras un diálogo: no
        ('label ch1_main:\n    scene bg a\n    $ x = 1\n    with dissolve\n    "y"\n', None, 0),                 # tras python: no
        ('label ch1_main:\n    scene bg a\n    if True:\n        with dissolve\n    "y"\n', None, 0),            # otro nivel: no
        ('label ch1_main:\n    scene bg a\n    show s 1a at t11\n    scene bg b\n    with dissolve\n    "y"\n', "dissolve", 5),
        ('label ch1_main:\n    scene bg a\n    play music t1\n    hide s\n    with fade\n    "y"\n', "fade", 5),
    ):
        d2 = os.path.join(tmp, "x"); os.makedirs(d2, exist_ok=True)
        open(os.path.join(d2, "s.rpy"), "w", encoding="utf-8").write(script)
        db2 = rpy_parser.load(d2)
        f2 = build_timeline(db2, "ch1_main")
        cur2 = se.find_scenes(db2, f2[-1])[0]
        if script.count("scene") == 2:                      # el `with` es del SEGUNDO scene; el primero no lo tiene
            first = se.find_scenes(db2, f2[0])[0]
            assert cur2.transition == "dissolve" and cur2.next_with_line == 5
        else:
            assert cur2.transition == want and cur2.next_with_line == line, (script, cur2.transition)

with tempfile.TemporaryDirectory() as tmp:
    # --- 2. REEMPLAZAR edita ese `with` en su sitio: nada de transición duplicada
    db, fr = setup(tmp)
    ctl = SceneController(tmp)
    ok, msg, _ = ctl.apply(db, fr[2], "replace", bgs(db)["bg club_day"], "fade", True, mt(tmp))
    t = rd(tmp)
    assert ok and t.count("with ") == 2 and "    scene bg club_day\n    show monika 1a at t11\n    with fade\n    m \"Tres\"" in t, t
    assert "wipeleft" not in t and "dissolve_scene_full" in t
    assert ctl.undo()[0] and rd(tmp) == SCRIPT
    ok, _, _ = ctl.apply(db, fr[2], "replace", bgs(db)["bg gris"], None, True, mt(tmp))      # sin transición: se quita ese with
    t = rd(tmp)
    assert ok and "    scene bg gris\n    show monika 1a at t11\n    m \"Tres\"" in t and "wipeleft" not in t, t
    assert ctl.undo()[0]
    ok, _, _ = ctl.apply(db, fr[2], "replace", bgs(db)["bg gris"], se.KEEP, True, mt(tmp))   # conservar: no toca el with
    assert ok and "    scene bg gris\n    show monika 1a at t11\n    with wipeleft\n" in rd(tmp)
    assert ctl.undo()[0]

    # --- 3. ELIMINAR quita la escena y su transición aunque esté tras los show; el fondo anterior vuelve a mandar
    ok, msg, _ = ctl.apply(db, fr[2], "delete", None, se.KEEP, True, mt(tmp))
    t = rd(tmp)
    assert ok and "class_day\n" not in t.split("label")[1] and "wipeleft" not in t and "    show monika 1a at t11\n    m \"Tres\"" in t, t
    assert "club_day" in msg
    db2 = rpy_parser.load(tmp)
    assert build_timeline(db2, "ch1_main")[2].stage.bg == "bg club_day"
    assert ctl.undo()[0] and rd(tmp) == SCRIPT

    # --- 4. AGREGAR vuelve a mostrar también las capas (dark, vignette...): un `scene` las borra
    assert se.reshow_lines(fr[1]) == ["show sayori 1a zorder 2 at t11", "show dark zorder 5"], se.reshow_lines(fr[1])
    ok, msg, _ = ctl.apply(db, fr[1], "add", bgs(db)["bg class_day"], se.KEEP, True, mt(tmp))
    t = rd(tmp)
    assert ok and "Ojo" not in msg
    assert ('    s "Uno"\n    scene bg class_day\n    show sayori 1a zorder 2 at t11\n    show dark zorder 5\n'
            '    with dissolve_scene_full\n    s "Dos"\n') in t, t
    db2 = rpy_parser.load(tmp)
    st = build_timeline(db2, "ch1_main")[1].stage
    assert st.bg == "bg class_day" and list(st.actors) == ["sayori"] and list(st.overlays) == ["dark"]
    assert ctl.undo()[0] and rd(tmp) == SCRIPT
    # sin volver a mostrar: solo el scene
    ok, _, _ = ctl.apply(db, fr[1], "add", bgs(db)["bg class_day"], None, False, mt(tmp))
    assert ok and '    s "Uno"\n    scene bg class_day\n    s "Dos"\n' in rd(tmp)
    assert ctl.undo()[0]
    # un diálogo que ya trae su scene justo antes: se avisa (el anterior solo se vería un instante)
    ok, msg, _ = ctl.apply(db, fr[2], "add", bgs(db)["bg club_day"], se.KEEP, False, mt(tmp))
    assert ok and "Ojo" in msg and "Reemplazar" in msg, msg

# --- 5. fondos de color: Solid(...), #rgb, con alfa
with tempfile.TemporaryDirectory() as tmp:
    db, fr = setup(tmp)
    b = bgs(db)
    assert (b["bg blanco"].kind, b["bg blanco"].color) == ("color", "#ffffff")
    assert (b["bg gris"].kind, b["bg gris"].color) == ("color", "#0a141e")
    assert (b["bg corto"].kind, b["bg corto"].color) == ("color", "#aabbcc")
    assert b["bg alfa"].color == "#123456"                                   # el alfa se ignora
    assert (b["bg raro"].kind, b["bg raro"].color) == ("image", "")          # Solid(variable): no se inventa un color
    assert b["dark"].kind == "color" and b["dark"].color == "#000000"        # no es bg, pero es un color sólido
    for e, c in (("Solid(\"#f80\")", "#ff8800"), ("Solid((1, 2, 3))", "#010203"), ("\"#abcd\"", "#aabbcc"),
                 ("\"bg/x.png\"", ""), ("Transform(\"a.png\")", ""), ("", "")):
        assert scenes.expr_color(e) == c, (e, scenes.expr_color(e))
print("OK")
