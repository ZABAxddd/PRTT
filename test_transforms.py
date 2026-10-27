"""Catálogo de efectos, expresiones `at` (modo avanzado) y animación de un sprite. Usa tus transforms.rpy y
definitions.rpy reales (están en la carpeta de PRTT)."""
import os
import shutil
import tempfile

import cells
import rpy_parser
import tfcatalog
from atl import TransformLib
from stage import build_timeline

HERE = os.path.dirname(os.path.abspath(__file__))

with tempfile.TemporaryDirectory() as game:
    for f in ("transforms.rpy", "definitions.rpy"):
        shutil.copy(os.path.join(HERE, f), game)
    with open(os.path.join(game, "script.rpy"), "w", encoding="utf-8") as f:
        f.write('define s = Character("Sayori", image="sayori")\n'
                'label ch1_main:\n'
                '    scene bg club_day\n'
                '    show sayori 1a zorder 2 at t21, hop(x=400, z=0.9)\n'
                '    show monika 1b at h22 with dissolve\n'
                '    s 1a "Hola"\n')
    db = rpy_parser.load(game)

    # --- 1. la lista son EFECTOS: nada de t11/t22/h21...
    eff = tfcatalog.effects(db)
    labels = [e.label for e in eff]
    for want in ("tcommon", "hop", "sink", "focus", "hop focus", "left in", "right in", "thide"):
        assert want in labels, (want, labels)
    assert not [e for e in eff if tfcatalog.is_position_name(e.name)], labels
    assert not any(l[:1] in "tihfsdlr" and l[1:].isdigit() for l in labels), labels
    assert labels.index("tcommon") < labels.index("hop") < labels.index("thide")
    assert "face" in labels and not [l for l in labels if l in ("t11", "t41", "h21")]   # los propios del mod, tras los de DDLC
    # --- 2. un efecto + una celda -> la variante que existe (hop, celda 1 de 2 -> h21)
    defined = set(db.transform_defs)
    by = {e.name: e for e in eff}
    assert tfcatalog.resolve(by["hop"], 2, 1, defined) == "h21"
    assert tfcatalog.resolve(by["hopfocus"], 3, 2, defined) == "hf32"
    assert tfcatalog.resolve(by["leftin"], 4, 4, defined) == "l44"
    assert tfcatalog.resolve(by["tcommon"], 1, 1, defined) == "t11"
    assert tfcatalog.resolve(by["panic"], 2, 1, defined) == "panic"          # sin variantes por celda
    assert tfcatalog.resolve(by["thide"], 2, 1, defined) == "thide"
    assert tfcatalog.resolve(by["hop"], 4, 5, defined) == "hop"              # celda imposible: el nombre del efecto
    # --- 3. al revés: del texto al efecto
    assert tfcatalog.effect_of("hf32", db).label == "hop focus"
    assert tfcatalog.effect_of("l21", db).label == "left in"
    assert tfcatalog.effect_of("t21, hop", db).label == "tcommon"
    assert tfcatalog.effect_of("hop(x=400)", db).label == "hop"
    assert tfcatalog.effect_of("mi_pos", db) is None
    assert tfcatalog.describe("hf21, shake", db) == "hop focus + shake"
    assert tfcatalog.describe("t21, hop(x=400, z=0.9)", db) == "tcommon + hop"
    # sin transforms.rpy: la lista base
    class _Empty: transforms = set(); transform_defs = {}
    assert [e.label for e in tfcatalog.effects(_Empty)][:4] == ["tcommon", "tinstant", "focus", "hop"]

    # --- 4. el parser y el escenario conservan las llamadas del `at`
    ev = [e for e in db.events if e.kind == "show"]
    assert ev[0].data["at"] == ["t21", "hop(x=400, z=0.9)"] and ev[0].data["zorder"] == 2
    assert ev[1].data["at"] == ["h22"] and ev[1].data["with"] == "dissolve"
    st = build_timeline(db, "ch1_main")[0].stage
    assert st.actors["sayori"].pos_name == "t21" and st.actors["sayori"].extra_transforms == ("hop(x=400, z=0.9)",)
    lay = cells.layout_from_stage(st)
    assert lay.at_of(1) == "t21, hop(x=400, z=0.9)" and lay.at_of(2) == "h22", (lay.at_of(1), lay.at_of(2))
    assert cells.to_rpy(lay).splitlines()[0] == "show sayori 1a zorder 2 at t21, hop(x=400, z=0.9)"
    assert cells.layout_from_stage(cells.apply_layout(build_timeline(db, "ch1_main")[0], lay).stage) == lay
    # raw: cualquier texto válido tras `at` (modo avanzado), y se rechaza lo imposible
    l = cells.CellLayout(2, {1: cells.Placement("sayori", ("1a",))})
    l.place_at("hop(x=400, z=0.9), mi_pos", cells.Placement("monika", ("1b",)), slot=2)
    assert l.at_of(2) == "hop(x=400, z=0.9), mi_pos" and l.count == 2
    l.set_count(3); assert l.at_of(2) == "hop(x=400, z=0.9), mi_pos"        # los nombres propios no cambian con la cantidad
    for bad in ("hop(", "hop)", "a b", "t21 # x", "hop(x=1))", "(x)"):
        try:
            cells.parse_transform_list(bad); raise SystemExit(f"debía fallar: {bad!r}")
        except ValueError:
            pass

    # --- 5. animación: el motor evalúa expresiones completas
    lib = TransformLib(db.transform_defs)
    assert lib.unknown_in("t21, nada_raro, hop(x=300)") == ["nada_raro"]
    rest = lib.final_expr("hop(x=400, z=0.9)", "show")
    assert abs(rest["xpos"] - 400) < 1e-6 and rest["yoffset"] == 0
    mid = lib.sample_expr("hop(x=400, z=0.9)", "show", None, 0.1)
    assert mid["yoffset"] < -15, mid                                          # a mitad del salto va arriba
    assert abs(lib.duration_expr("hop", "show") - 0.2) < 1e-6
    assert lib.duration_expr("h21", "show") > 0 and lib.duration_expr("t21", "show") >= 0.25
    assert lib.duration_expr("tinstant", "show") == 0 and lib.duration_expr("i21", "show") == 0
    assert lib.loops_expr("panic", "show") and not lib.loops_expr("hop", "show")
    s0, s1 = lib.sample_expr("t21", "show", None, 0.0), lib.final_expr("t21", "show")
    assert s0["alpha"] == 0 and s1["alpha"] == 1 and abs(s1["xpos"] - 400) < 1e-6      # entra con fundido a su celda
    assert lib.final_expr("thide", "hide", lib.final_expr("t21", "show"))["alpha"] == 0     # tras la salida ya no se ve
    gone = lib.sample_expr("thide", "hide", lib.final_expr("t21", "show"), 0.5)
    assert gone["alpha"] == 0, gone                                                    # al terminar la salida ya no se ve
    chain = lib.final_expr("t21, hop(x=300)", "show")
    assert abs(chain["xpos"] - 300) < 1e-6                                              # el posterior pisa lo que toca
    moved = lib.sample_expr("t21", "replace", lib.final_expr("t22", "show"), 0.05)
    assert 400 < moved["xpos"] < 880, moved                                             # cambio de celda: va de 880 a 400

    # --- 6. el motor dibuja SOLO al personaje y la imagen cambia con el tiempo (sin PNG: usa el marcador)
    try:
        import preview
    except ImportError:
        preview = None
    if preview is not None and preview.HAVE_PIL:
        pv = preview.Preview(db, game)
        a = pv.sprite_frame("sayori", ("1a",), "hop", "show", 0.1)
        b = pv.sprite_frame("sayori", ("1a",), "hop", "show", None)
        assert a.size == (1280, 720) and b.size == (1280, 720) and a.tobytes() != b.tobytes()
        h = pv.sprite_frame("sayori", ("1a",), "thide", "hide", 0.5)
        assert h.getextrema() != b.getextrema() or h.tobytes() != b.tobytes()
        r = pv.sprite_frame("sayori", ("1a",), "t21", "replace", 0.05, start_expr="t22")
        assert r.tobytes() != pv.sprite_frame("sayori", ("1a",), "t21", "replace", None, start_expr="t22").tobytes()
print("OK")
