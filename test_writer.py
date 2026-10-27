import os
import tempfile

import cells
import rpy_parser
import rpy_writer
from cells import CellLayout, Placement, layout_from_stage
from history import EditHistory
from stage import build_timeline

SCRIPT = (
    'label ch1_main:\n'
    '    scene bg club_day\n'
    '    show sayori 1a at t11\n'
    '    s 1a "Hola"\n'
    '    if True:\n'
    '        show monika 1b at t22\n'
    '        m "Dentro de un if"\n'
    '    "Narrador"\n'
)


def make(tmp, text=SCRIPT, bom=False, crlf=False, name="script.rpy"):
    path = os.path.join(tmp, name)
    data = text.replace("\n", "\r\n") if crlf else text
    with open(path, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8"))
    return path


def load(tmp):
    return rpy_parser.load(tmp)


def change(db, idx, lay, key=None):
    fr = build_timeline(db, "ch1_main")
    return rpy_writer.make_change(key or ("ch1_main", idx), fr[idx], lay, fr[idx - 1] if idx else None, db), fr[idx]


def read(path):
    with open(path, "rb") as f:
        return f.read()


def mt(tmp, name="script.rpy"):
    return {name: os.stat(os.path.join(tmp, name)).st_mtime_ns}


with tempfile.TemporaryDirectory() as tmp:
    path = make(tmp)
    original = read(path)
    w = rpy_writer.ScriptWriter(tmp)

    # --- 1. REEMPLAZO: el `show sayori` que ya existía se reescribe en su sitio; monika entra justo debajo
    db = load(tmp)
    lay = layout_from_stage(build_timeline(db, "ch1_main")[0].stage)
    lay.place_at("t22", Placement("monika", ("1b",), 2))
    ch, _ = change(db, 0, lay)
    res = w.apply([ch], mt(tmp))
    assert [r.ok for r in res] == [True], res
    text = read(path).decode()
    assert text == SCRIPT.replace(
        '    show sayori 1a at t11\n',
        '    show sayori 1a zorder 0 at t21\n    show monika 1b zorder 2 at t22\n'), text
    assert "#" not in text, "sin comentarios automáticos"
    assert text.count("show sayori") == 1                                 # no se duplicó
    assert read(path + ".prtt-bak") == original

    # --- 2. se relee igual (sin marcadores: el script ES el estado)
    db = load(tmp)
    fr0 = build_timeline(db, "ch1_main")[0]
    a = fr0.stage.actors
    assert a["sayori"].pos_name == "t21" and a["monika"].pos_name == "t22" and a["monika"].zorder == 2
    assert layout_from_stage(fr0.stage) == lay

    # --- 3. dentro de un `if`: se reemplaza el show de monika por hide (ya estaba en escena) y se añade el de sayori
    lay2 = layout_from_stage(build_timeline(db, "ch1_main")[1].stage)
    lay2.set_count(0)
    ch2, _ = change(db, 1, lay2)
    assert [r.ok for r in w.apply([ch2], mt(tmp))] == [True]
    text = read(path).decode()
    assert '        hide monika\n        hide sayori\n        m "Dentro' in text, text
    assert "show monika 1b at t22" not in text and "#" not in text
    db = load(tmp)
    assert not build_timeline(db, "ch1_main")[1].stage.actors

    # --- 4. volver a guardar el mismo diálogo REEMPLAZA esas líneas (no duplica)
    lay3 = layout_from_stage(build_timeline(db, "ch1_main")[0].stage)
    lay3.set_zorder(1, 5)
    ch3, _ = change(db, 0, lay3)
    assert [r.ok for r in w.apply([ch3], mt(tmp))] == [True]
    text = read(path).decode()
    assert text.count("show sayori") == 1 and "zorder 5 at t21" in text and "zorder 0 at t21" not in text
    assert text.count("show monika") == 1
    db = load(tmp)
    assert [r.ok for r in w.apply([change(db, 0, lay3)[0]], mt(tmp))] == [True]   # idéntico: no cambia nada
    assert read(path).decode() == text

    # --- 5. deshacer hasta el original: vuelve EXACTAMENTE el archivo original
    db = load(tmp)
    chs = [change(db, 0, None)[0], change(db, 1, None)[0]]
    assert all(r.ok for r in w.apply(chs, mt(tmp)))
    assert read(path) == original

    # --- 6. quitar a todos: el show que metía al personaje se BORRA (no queda un hide huérfano)
    db = load(tmp)
    lay_n = layout_from_stage(build_timeline(db, "ch1_main")[0].stage)
    lay_n.set_count(0)
    ch, _ = change(db, 0, lay_n)
    w.apply([ch], mt(tmp))
    text = read(path).decode()
    assert "show sayori" not in text and "hide sayori" not in text, text
    db = load(tmp)
    assert not build_timeline(db, "ch1_main")[0].stage.actors
    lay_m = CellLayout(1, {1: Placement("natsuki", ("1a",))})
    ch, _ = change(db, 0, lay_m)
    w.apply([ch], mt(tmp))
    text = read(path).decode()
    assert '    scene bg club_day\n    show natsuki 1a zorder 0 at t11\n    s 1a "Hola"' in text, text
    db = load(tmp)
    assert list(build_timeline(db, "ch1_main")[0].stage.actors) == ["natsuki"]
    assert all(r.ok for r in w.apply([change(db, 0, None)[0]], mt(tmp)))
    assert read(path) == original                                          # y se deshace entero (dos guardados seguidos)

with tempfile.TemporaryDirectory() as tmp:
    # --- 7. BOM y CRLF se conservan; archivo cambiado en disco / línea movida -> no escribe
    path = make(tmp, bom=True, crlf=True)
    original = read(path)
    db = load(tmp)
    lay = layout_from_stage(build_timeline(db, "ch1_main")[0].stage)
    lay.set_zorder(1, 3)
    ch, _ = change(db, 0, lay)
    w = rpy_writer.ScriptWriter(tmp)
    r = w.apply([ch], {"script.rpy": os.stat(path).st_mtime_ns - 1})
    assert not r[0].ok and "cambió en disco" in r[0].msg and read(path) == original
    bad = rpy_writer.Change(ch.key, ch.file, ch.line + 1, ch.raw, lay)
    r = w.apply([bad], mt(tmp))
    assert not r[0].ok and "no coincide" in r[0].msg and read(path) == original
    assert [x.ok for x in w.apply([ch], mt(tmp))] == [True]
    data = read(path)
    assert data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert b"    show sayori 1a zorder 3 at t11\r\n    s 1a" in data and b"#" not in data

with tempfile.TemporaryDirectory() as tmp:
    # --- 8. no UTF-8: se niega a tocarlo
    path = os.path.join(tmp, "script.rpy")
    with open(path, "wb") as f:
        f.write(SCRIPT.replace("Hola", "Holá").encode("latin-1"))
    ch = rpy_writer.Change("k", "script.rpy", 4, 's 1a "Hola"', CellLayout(1, {1: Placement("sayori", ("1a",))}))
    r = rpy_writer.ScriptWriter(tmp).apply([ch])
    assert not r[0].ok and "UTF-8" in r[0].msg

with tempfile.TemporaryDirectory() as tmp:
    # --- 9. comentarios: apagados por defecto; con comments=True cada línea escrita acaba en "# PRTT"
    path = make(tmp)
    db = load(tmp)
    lay = layout_from_stage(build_timeline(db, "ch1_main")[0].stage)
    lay.place_at("t22", Placement("monika", ("1b",), 2))
    w = rpy_writer.ScriptWriter(tmp, comments=True)
    w.apply([change(db, 0, lay)[0]], mt(tmp))
    text = read(path).decode()
    assert "show sayori 1a zorder 0 at t21  # PRTT\n" in text and "show monika 1b zorder 2 at t22  # PRTT\n" in text, text
    db = load(tmp)                                   # el parser ignora el comentario
    assert layout_from_stage(build_timeline(db, "ch1_main")[0].stage) == lay
    w.comments = False                               # al apagar la opción, el siguiente guardado los retira
    lay.set_zorder(1, 1)
    w.apply([change(db, 0, lay)[0]], mt(tmp))
    assert "#" not in read(path).decode()

with tempfile.TemporaryDirectory() as tmp:
    # --- 10. se conservan `with`, `behind`, `onlayer` y el comentario del usuario de la línea reemplazada
    src = ('label ch1_main:\n    scene bg club_day\n'
           '    show sayori 1a at t11 with dissolve  # entra sayori\n'
           '    s 1a "Hola"\n')
    path = make(tmp, src)
    db = load(tmp)
    lay = layout_from_stage(build_timeline(db, "ch1_main")[0].stage)
    lay.place_at("t22", Placement("monika", ("1b",)))
    rpy_writer.ScriptWriter(tmp).apply([change(db, 0, lay)[0]], mt(tmp))
    text = read(path).decode()
    assert "    show sayori 1a zorder 0 at t21 with dissolve  # entra sayori\n" in text, text
    assert "    show monika 1b zorder 0 at t22\n" in text

with tempfile.TemporaryDirectory() as tmp:
    # --- 11. bloques `# PRTT:begin/end` de versiones anteriores: se retiran al volver a guardar
    src = ('label ch1_main:\n    scene bg club_day\n'
           '    # PRTT:begin\n    show sayori 1a zorder 0 at t21\n    # PRTT:end\n'
           '    s 1a "Hola"\n')
    path = make(tmp, src)
    db = load(tmp)
    lay = layout_from_stage(build_timeline(db, "ch1_main")[0].stage)
    lay.set_zorder(1, 3)
    rpy_writer.ScriptWriter(tmp).apply([change(db, 0, lay)[0]], mt(tmp))
    text = read(path).decode()
    assert text == ('label ch1_main:\n    scene bg club_day\n    show sayori 1a zorder 3 at t21\n    s 1a "Hola"\n'), text
    make(tmp, src)                                    # y `layout=None` quita el bloque antiguo entero
    db = load(tmp)
    rpy_writer.ScriptWriter(tmp).apply([change(db, 0, None)[0]], mt(tmp))
    assert read(path).decode() == 'label ch1_main:\n    scene bg club_day\n    s 1a "Hola"\n'

with tempfile.TemporaryDirectory() as tmp:
    # --- 12. el show de un diálogo ANTERIOR no se toca: se inserta uno nuevo en la ventana de este
    src = ('label ch1_main:\n    scene bg club_day\n    show sayori 1a at t11\n'
           '    s 1a "Uno"\n    s 1a "Dos"\n')
    path = make(tmp, src)
    db = load(tmp)
    lay = layout_from_stage(build_timeline(db, "ch1_main")[1].stage)
    lay.place_at("t22", Placement("monika", ("1b",)))
    rpy_writer.ScriptWriter(tmp).apply([change(db, 1, lay)[0]], mt(tmp))
    assert read(path).decode() == (
        'label ch1_main:\n    scene bg club_day\n    show sayori 1a at t11\n    s 1a "Uno"\n'
        '    show sayori 1a zorder 0 at t21\n    show monika 1b zorder 0 at t22\n    s 1a "Dos"\n')

with tempfile.TemporaryDirectory() as tmp:
    # --- 13. una instrucción en varias líneas no se reescribe a medias: se avisa y no se toca nada
    src = ('label ch1_main:\n    scene bg club_day\n    show sayori 1a \\\n        at t11\n    s 1a "Hola"\n')
    path = make(tmp, src)
    original = read(path)
    db = load(tmp)
    fr = build_timeline(db, "ch1_main")[0]
    assert "sayori" in fr.stage.actors
    lay = layout_from_stage(fr.stage)
    lay.set_zorder(1, 2)
    r = rpy_writer.ScriptWriter(tmp).apply([change(db, 0, lay)[0]], mt(tmp))
    assert not r[0].ok and "varias líneas" in r[0].msg and read(path) == original

with tempfile.TemporaryDirectory() as tmp:
    # --- 14. texto de los diálogos: sustituye la línea exacta, con su sangría, saltos y comentario
    src = ('label ch1_main:\n    show sayori 1a at t11\n    s 1a "Hola"  # saludo\n'
           '    if True:\n        m "Dentro"\n    "Narrador"\n')
    path = make(tmp, src, crlf=True)
    w = rpy_writer.ScriptWriter(tmp)
    db = load(tmp)
    assert w.read_statements("script.rpy", [3, 5]) == [('s 1a "Hola"', "# saludo"), ('m "Dentro"', "")]
    edits = [rpy_writer.DialogueEdit(0, "script.rpy", 3, 's 1a "Hola"', 's 1b "Hola, \\"mundo\\""'),
             rpy_writer.DialogueEdit(1, "script.rpy", 5, 'm "Dentro"', 'm "Fuera" with dissolve')]
    assert [r.ok for r in w.apply_dialogues(edits, mt(tmp))] == [True, True]
    data = read(path)
    assert b'    s 1b "Hola, \\"mundo\\""  # saludo\r\n' in data and b'        m "Fuera" with dissolve\r\n' in data, data
    assert b"\n" not in data.replace(b"\r\n", b"")
    db = load(tmp)
    say = [e for e in db.events if e.kind == "say"]
    assert say[0].data["text"] == 'Hola, "mundo"' and say[0].data["attrs"] == ["1b"] and len(say) == 3
    before = read(path)
    bad = [rpy_writer.DialogueEdit(0, "script.rpy", 3, 's 1b "Hola, \\"mundo\\""', 's 1b "sin cerrar'),
           rpy_writer.DialogueEdit(1, "script.rpy", 5, 'm "OTRA COSA"', 'm "x"'),            # ya no es la que se mostró
           rpy_writer.DialogueEdit(2, "script.rpy", 6, '"Narrador"', 'show sayori 1a'),       # ya no sería un diálogo
           rpy_writer.DialogueEdit(3, "script.rpy", 6, '"Narrador"', '"Una\nlínea"')]         # varias líneas
    res = w.apply_dialogues(bad, mt(tmp))
    assert [r.ok for r in res] == [False] * 4 and read(path) == before, res
    assert w.apply_dialogues(edits[:1], {"script.rpy": 1})[0].ok is False                    # archivo cambiado fuera

SCRIPT2 = (
    'define s = Character("Sayori", image="sayori")\n'
    'define m = Character("Monika", image="monika")\n'
    '\n'
    'label ch1_main:\n'
    '    scene bg club_day\n'
    '    show sayori 1a at t21\n'
    '    show monika 1b at t22\n'
    '    s "Uno"\n'
    '    m "Dos"\n'
    '    s 1a "Tres"\n'
)


def lay_of(db, idx, **edits):
    lay = layout_from_stage(build_timeline(db, "ch1_main")[idx].stage)
    for tag, attrs in edits.items():
        k = next(k for k, p in lay.slots.items() if p.tag == tag)
        lay.slots[k].attrs = tuple(attrs.split())
    return lay


with tempfile.TemporaryDirectory() as tmp:
    # --- 15. sin duplicados: lo que no cambia no genera ninguna línea (ya existe una instrucción equivalente)
    path = make(tmp, SCRIPT2)
    original = read(path)
    w = rpy_writer.ScriptWriter(tmp)
    db = load(tmp)
    assert [r.ok for r in w.apply([change(db, 1, lay_of(db, 1))[0]], mt(tmp))] == [True]
    assert read(path) == original

    # --- 16. solo cambia la pose de QUIEN HABLA: sintaxis compacta, sobreescribiendo su línea de diálogo
    ch, _ = change(db, 1, lay_of(db, 1, monika="1c"))
    assert ch.speaker == "monika"
    w.apply([ch], mt(tmp))
    assert read(path).decode() == SCRIPT2.replace('    m "Dos"\n', '    m 1c "Dos"\n'), read(path).decode()
    db = load(tmp)
    assert build_timeline(db, "ch1_main")[1].stage.actors["monika"].attrs == ("1c",)
    w.apply([change(db, 1, lay_of(db, 1, monika="1d"))[0]], mt(tmp))             # otra vez: se SOBREESCRIBE la misma línea
    assert read(path).decode() == SCRIPT2.replace('    m "Dos"\n', '    m 1d "Dos"\n')
    db = load(tmp)
    assert all(r.ok for r in w.apply([change(db, 1, None)[0]], mt(tmp))) and read(path) == original

    # --- 17. solo cambia la pose de OTRO personaje: `show monika 1b` simple (sin zorder ni at); se reemplaza al repetir
    db = load(tmp)
    w.apply([change(db, 1, lay_of(db, 1, sayori="1b"))[0]], mt(tmp))
    assert read(path).decode() == SCRIPT2.replace('    m "Dos"\n', '    show sayori 1b\n    m "Dos"\n'), read(path).decode()
    db = load(tmp)
    w.apply([change(db, 1, lay_of(db, 1, sayori="1c"))[0]], mt(tmp))
    text = read(path).decode()
    assert text == SCRIPT2.replace('    m "Dos"\n', '    show sayori 1c\n    m "Dos"\n'), text
    assert text.count("show sayori") == 2                                       # el de arriba + el de este diálogo
    # --- 18. si además cambia zorder: la línea pasa a la forma completa (también reemplazándola, sin duplicar)
    db = load(tmp)
    lay = lay_of(db, 1, sayori="1c")
    lay.set_zorder(1, 3)
    w.apply([change(db, 1, lay)[0]], mt(tmp))
    text = read(path).decode()
    assert text == SCRIPT2.replace('    m "Dos"\n', '    show sayori 1c zorder 3 at t21\n    m "Dos"\n'), text
    db = load(tmp)
    assert all(r.ok for r in w.apply([change(db, 1, None)[0]], mt(tmp))) and read(path) == original

    # --- 19. quien habla CON pose en su línea y recibe zorder/at: show completo + la línea de diálogo sincronizada
    db = load(tmp)
    lay = lay_of(db, 2, sayori="1b")
    lay.set_zorder(1, 2)
    w.apply([change(db, 2, lay)[0]], mt(tmp))
    text = read(path).decode()
    assert text == SCRIPT2.replace('    s 1a "Tres"\n', '    show sayori 1b zorder 2 at t21\n    s 1b "Tres"\n'), text
    assert text.count("show sayori") == 2
    db = load(tmp)
    assert build_timeline(db, "ch1_main")[2].stage.actors["sayori"].attrs == ("1b",)
    # editar el TEXTO del diálogo entre medias no impide deshacer lo de las poses
    ed = rpy_writer.DialogueEdit(0, "script.rpy", db.events[-1].line, 's 1b "Tres"', 's 1b "Cuatro"')
    assert w.apply_dialogues([ed], mt(tmp))[0].ok
    db = load(tmp)
    assert all(r.ok for r in w.apply([change(db, 2, None)[0]], mt(tmp)))
    assert read(path).decode() == SCRIPT2.replace('"Tres"', '"Cuatro"')

# --- 9. historial
L = lambda z: CellLayout(1, {1: Placement("sayori", ("1a",), z)})
h = EditHistory(limit=3)
edits = {}
assert not h.dirty(edits) and not h.can_undo()
for z in (1, 2):
    before = h.snapshot(edits)
    edits[("a", 0)] = L(z)
    assert h.record(before, edits, f"z{z}")
assert h.dirty(edits) and h.pending(edits) == 1
assert not h.record(h.snapshot(edits), edits, "nada")            # sin cambios: no se anota
desc, back = h.undo(edits); edits = back
assert desc == "z2" and edits[("a", 0)].slots[1].zorder == 1 and h.can_redo()
desc, fwd = h.redo(edits); edits = fwd
assert desc == "z2" and edits[("a", 0)].slots[1].zorder == 2
h.mark_saved(("a", 0), edits[("a", 0)])
assert not h.dirty(edits) and h.pending(edits) == 0
edits, = [h.undo(edits)[1]]                                      # deshacer tras guardar -> pendiente
assert h.dirty(edits) and h.pending(edits) == 1
h.undo(edits)                                                    # sigue habiendo historial
edits = {}
assert h.dirty(edits)                                            # el bloque guardado debería quitarse
h.mark_saved(("a", 0), None)
assert not h.dirty(edits)
before = h.snapshot(edits)
for i in range(6):
    b = h.snapshot(edits); edits[("a", i)] = L(i); h.record(b, edits, str(i))
assert len(h._undo) == 3                                         # tope del historial
b = h.snapshot(edits); edits[("a", 9)] = L(9); h.record(b, edits, "x")
assert not h.can_redo()
print("OK")
