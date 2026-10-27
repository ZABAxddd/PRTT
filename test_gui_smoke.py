"""Prueba de humo SIN pantalla: sustituye tkinter por un doble y ejercita la lógica real de
DialogueBrowser (ediciones, historial, guardado en el .rpy, relectura, cierre)."""
import json
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock


class FakeModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock(name=name)


class FakeVar:
    def __init__(self, value=None):
        self.v = value
        self.cbs = []

    def get(self):
        return self.v

    def set(self, v):
        self.v = v
        for cb in list(self.cbs):
            cb()

    def trace_add(self, _mode, cb):
        self.cbs.append(lambda: cb())


class FakeTk:
    def __init__(self, *a, **k):
        self.titles = []
        self.cancelled = []
        self.destroyed = False

    def title(self, t=None):
        if t is not None:
            self.titles.append(t)

    def after(self, ms, fn=None):
        return ("job", ms)

    def after_cancel(self, job):
        self.cancelled.append(job)

    def destroy(self):
        self.destroyed = True

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock(name=name)


tk = FakeModule("tkinter")
tk.Tk = tk.Toplevel = FakeTk
tk.BooleanVar = tk.IntVar = tk.StringVar = FakeVar
ttk = FakeModule("tkinter.ttk")
ttk.LabelFrame = FakeTk
tk.ttk = ttk
tk.messagebox = MagicMock()
tk.filedialog = MagicMock()
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk,
                    "tkinter.messagebox": tk.messagebox, "tkinter.filedialog": tk.filedialog})

import rpy_parser  # noqa: E402
import prtt_gui  # noqa: E402
from stage import build_timeline  # noqa: E402

SCRIPT = (
    'label ch1_main:\n'
    '    scene bg club_day\n'
    '    show sayori 1a at t11\n'
    '    s 1a "Hola"\n'
    '    m "Segundo"\n'
)

_cfg = tempfile.mkdtemp()
prtt_gui.SETTINGS_FILE = os.path.join(_cfg, "prtt_settings.json")      # no tocar la carpeta real de la herramienta

with tempfile.TemporaryDirectory() as game:
    path = os.path.join(game, "script.rpy")
    open(path, "w", encoding="utf-8").write(SCRIPT)
    db = rpy_parser.load(game)
    b = prtt_gui.DialogueBrowser(db, game)
    b._pv = MagicMock()
    b._pv_draw = lambda *a, **k: None
    b._pv_frames = b._timeline("ch1_main")
    b._pv_i = 0
    assert b.title is not None and not b._is_dirty()

    # --- editar: añadir a monika en t22
    b._pv_edit(lambda L: L.place_at("t22", prtt_gui.cells.Placement("monika", ("1b",), 2)))
    assert b._is_dirty() and b.hist.can_undo() and b.titles[-1].startswith("* "), b.titles
    assert b._autosave_job == ("job", prtt_gui.AUTOSAVE_DELAY_MS)       # auto-guardado programado

    # --- guardar -> escribe el bloque y relee (números de línea nuevos)
    assert b.save(auto=True) is True
    text = open(path, encoding="utf-8").read()
    assert "    show sayori 1a zorder 0 at t21\n    show monika 1b zorder 2 at t22\n    s 1a" in text, text
    assert "#" not in text and text.count("show sayori") == 1           # sin comentarios; se reemplazó, no se duplicó
    assert not b._is_dirty() and not b.titles[-1].startswith("* ")
    assert b._pv_frames[0].event.line == 5                                # el show de sayori se reescribió y monika entró debajo
    assert list(b._pv_frames[0].stage.actors) == ["sayori", "monika"]

    # --- otra edición del MISMO diálogo tras reparsear: sigue la clave estable y reemplaza el bloque
    b._pv_edit(lambda L: L.set_zorder(1, 4))
    assert b._is_dirty()
    assert b.save() is True
    text = open(path, encoding="utf-8").read()
    assert text.count("show monika") == 1 and "zorder 4 at t21" in text and "zorder 0 at t21" not in text

    # --- deshacer (tras guardar) -> pendiente; guardar escribe el estado anterior
    b._undo()
    assert b._is_dirty() and b._pv_edits[("ch1_main", 0)].slots[1].zorder == 0
    b.save()
    assert "zorder 0 at t21" in open(path, encoding="utf-8").read()
    # --- deshacer hasta el principio -> el bloque desaparece y el archivo vuelve a ser el original
    b._undo()
    assert not b._pv_edits and b._is_dirty()
    b.save()
    assert open(path, encoding="utf-8").read() == SCRIPT and not b._is_dirty()
    # --- rehacer lo deshecho
    b._redo()
    assert ("ch1_main", 0) in b._pv_edits and b._is_dirty()

    # --- toggle de celdas: oculto = no se dibuja nada ni queda geometría clicable
    b._pv_cells_on.set(False)
    b._pv_rects = [("x",)]
    b._pv_draw_cells()
    assert b._pv_rects == [] and b._pv_zw == []     # sin celdas visibles no hay geometría clicable ni cuadritos Z
    b._pv_canvas.delete.assert_called_with("cell")

    # --- cerrar con cambios: Cancelar / No guardar / Guardar
    class Dlg:
        def __init__(self, c):
            self.choice = c

    for choice, destroyed, saved in (("cancel", False, False), ("discard", True, False), ("save", True, True)):
        b.destroyed = False
        prtt_gui._AskSave = lambda master, n, c=choice: Dlg(c)
        b._on_close()
        assert b.destroyed is destroyed, choice
        assert (("show monika" in open(path, encoding="utf-8").read()) is saved), choice
        if choice == "cancel":
            assert b._is_dirty()
    # --- un guardado que falla (archivo cambiado fuera) NO deja cerrar con "Guardar"
    b._pv_edits.clear(); b.hist.saved.clear()
    b._pv_edit(lambda L: L.place_at("t22", prtt_gui.cells.Placement("natsuki", ("1a",))))
    with open(path, "a", encoding="utf-8") as f:
        f.write("# cambio externo\n")
    os.utime(path, ns=(1, 1))
    b.destroyed = False
    prtt_gui._AskSave = lambda master, n: Dlg("save")
    b._on_close()
    assert b.destroyed is False and b._is_dirty()
    tk.messagebox.showerror.assert_called()
    # --- «Recargar .rpy» re-sincroniza y el siguiente guardado funciona
    b._reload_from_disk()
    assert b.save() is True and "natsuki" in open(path, encoding="utf-8").read()
    # --- atajos de teclado globales: Ctrl+Z / Ctrl+Y / Ctrl+Mayús+Z, con Bloq Mayús y en otra pestaña
    class Ev:
        def __init__(self, keysym, state=0, keycode=0):
            self.keysym, self.state, self.keycode = keysym, state, keycode

    b.grab_current = lambda: None
    b._editing_text = lambda: False
    b._pv_edits.clear(); b.hist.saved.clear(); b.hist._undo.clear(); b.hist._redo.clear()
    b._pv_edit(lambda L: L.place_at("t22", prtt_gui.cells.Placement("monika", ("1b",), 2)))
    assert b._on_ctrl_key(Ev("z")) == "break" and not b._pv_edits          # Ctrl+Z
    assert b._on_ctrl_key(Ev("y")) == "break" and b._pv_edits              # Ctrl+Y
    assert b._on_ctrl_key(Ev("z")) == "break" and not b._pv_edits
    assert b._on_ctrl_key(Ev("Z", state=0x1)) == "break" and b._pv_edits   # Ctrl+Mayús+Z
    assert b._on_ctrl_key(Ev("Z", state=0x2)) == "break" and not b._pv_edits   # Bloq Mayús + Ctrl+Z = deshacer
    assert b._on_ctrl_key(Ev("x")) is None                                 # otras teclas: no se tocan
    b._editing_text = lambda: True
    assert b._on_ctrl_key(Ev("y")) is None and not b._pv_edits             # escribiendo en un campo: lo gestiona el campo
    b._editing_text = lambda: False
    b._modal = 1
    assert b._on_ctrl_key(Ev("y")) is None and not b._pv_edits             # con un diálogo modal abierto: nada
    b._modal = 0
    b._pv_edits.clear(); b.hist.saved.clear(); b.hist._undo.clear(); b.hist._redo.clear()
    b.save()                                                                # deja el .rpy como al principio de este bloque
    open(path, "w", encoding="utf-8").write(SCRIPT); b._reload_from_disk()

    # --- transform libre de extremo a extremo: se escribe en el .rpy y se relee igual
    b._pv_frames = b._timeline("ch1_main"); b._pv_i = 0
    b._pv_edit(lambda L: L.place_at("mi_pos", prtt_gui.cells.Placement("monika", ("1b",), 2), slot=2))
    assert b.save() is True
    assert "show monika 1b zorder 2 at mi_pos" in open(path, encoding="utf-8").read()
    b._pv_frames = b._timeline("ch1_main"); b._pv_i = 0
    assert prtt_gui.cells.layout_from_stage(b._pv_frames[0].stage).at_of(2) == "mi_pos"

# ======================================================================================
# Segunda ventana: comentarios opcionales, edición de diálogos, arrastre y remoción a la derecha
# ======================================================================================
SCRIPT2 = (
    'define s = Character("Sayori", image="sayori")\n'
    'define m = Character("Monika", image="monika")\n'
    'label ch1_main:\n'
    '    scene bg club_day\n'
    '    show sayori 1a at t21\n'
    '    show monika 1b at t22\n'
    '    s "Uno"\n'
    '    m "Dos"\n'
)


class Mouse:
    def __init__(self, x, y):
        self.x, self.y, self.x_root, self.y_root = x, y, x, y


with tempfile.TemporaryDirectory() as game2:
    path2 = os.path.join(game2, "script.rpy")
    open(path2, "w", encoding="utf-8").write(SCRIPT2)
    db2 = rpy_parser.load(game2)
    b2 = prtt_gui.DialogueBrowser(db2, game2)
    b2._pv = MagicMock()
    b2._pv_draw = lambda *a, **k: None
    b2._pv_frames = b2._timeline("ch1_main")
    b2._pv_i = 1
    KEY = ("ch1_main", 1)

    # --- el dropdown de cantidad: bajar de 2 a 1 quita al de la DERECHA, sin ninguna alerta, y Ctrl+Z lo devuelve
    tk.messagebox.askyesno.reset_mock()
    b2._pv_count.set(1)
    b2._pv_count_changed()
    assert [p.tag for p in b2._pv_edits[KEY].slots.values()] == ["sayori"] and b2._pv_edits[KEY].count == 1
    assert not tk.messagebox.askyesno.called and not tk.messagebox.showinfo.called
    b2._undo()
    assert KEY not in b2._pv_edits or len(b2._pv_edits[KEY].slots) == 2

    # --- arrastrar una celda ocupada sobre otra ocupada: se desplaza al ocupante a la vacía más próxima
    b2._pv_rects = [(1, "t21", 0, 0, 100, 100), (2, "t22", 100, 0, 200, 100)]
    b2._pv_geom = (0, 0, 1.0)
    b2._pv_press(Mouse(10, 10))
    b2._pv_motion(Mouse(12, 11))
    assert b2._pv_drag_src is None                                  # todavía es un clic
    b2._pv_motion(Mouse(150, 20))
    assert b2._pv_drag_src == 1
    b2._pv_release(Mouse(150, 20))
    assert {k: p.tag for k, p in b2._pv_edits[KEY].slots.items()} == {1: "monika", 2: "sayori"}
    assert b2._pv_drag_src is None and b2._pv_press_info is None
    b2._undo()
    # un arrastre soltado fuera de las celdas no cambia nada; un clic sin mover abre el menú
    n_hist = len(b2.hist._undo)
    b2._pv_press(Mouse(10, 10)); b2._pv_motion(Mouse(300, 10)); b2._pv_release(Mouse(300, 10))
    assert len(b2.hist._undo) == n_hist
    b2._pv_press(Mouse(10, 10)); b2._pv_release(Mouse(10, 10))
    assert b2._pv_sel == 1

    # --- opción de comentarios: apagada por defecto, se guarda como preferencia y solo entonces se escribe "# PRTT"
    assert b2._write_comments.get() is False and b2.writer.comments is False
    b2._write_comments.set(True)
    b2._on_comments_toggle()
    assert b2.writer.comments is True and json.load(open(prtt_gui.SETTINGS_FILE))["write_comments"] is True
    b2._pv_edit(lambda L: L.set_zorder(1, 3))
    assert b2.save() is True
    assert "show sayori 1a zorder 3 at t21  # PRTT\n" in open(path2, encoding="utf-8").read()
    b2._write_comments.set(False)
    b2._on_comments_toggle()
    b2._pv_edit(lambda L: L.set_zorder(1, 4))
    assert b2.save() is True
    assert "#" not in open(path2, encoding="utf-8").read()

    # --- sobreescribir un diálogo: reescribe SU línea y deja lista, índice y detalle sincronizados
    dls = [dl for infos in b2.index.values() for i in infos for dl in i.lines]
    dl = dls[1]
    old = b2.writer.read_statements(dl.file, [dl.line])[0][0]
    assert old == 'm "Dos"'
    assert b2._apply_dialogue_edits([dl], [old], ['m 1c "Nuevo \\"texto\\""']) is True
    assert 'm 1c "Nuevo \\"texto\\""' in open(path2, encoding="utf-8").read()
    assert dl.text == 'Nuevo "texto"' and dl.attrs == ["1c"], (dl.text, dl.attrs)
    assert b2._timeline("ch1_main")[1].event.data["text"] == 'Nuevo "texto"'
    assert b2._is_dirty() is False                                  # las ediciones de celdas ya estaban guardadas
    # sin cambios no escribe; con el archivo cambiado por fuera, no escribe y avisa
    before = open(path2, encoding="utf-8").read()
    assert b2._apply_dialogue_edits([dl], [dl.raw_text], [dl.raw_text]) is True
    open(path2, "a", encoding="utf-8").write("# fuera\n")
    os.utime(path2, ns=(1, 1))
    tk.messagebox.showerror.reset_mock()
    cur = b2.writer.read_statements(dl.file, [dl.line])[0][0]
    assert b2._apply_dialogue_edits([dl], [cur], ['m "x"']) is False and tk.messagebox.showerror.called
    assert 'm "x"' not in open(path2, encoding="utf-8").read()

# ======================================================================================
# Tercera parte: selector por EFECTOS (sin t11/t22...), modo avanzado y ventana de animación
# ======================================================================================
import shutil  # noqa: E402
import cells  # noqa: E402
from sprites import SpriteBook  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT3 = (
    'define s = Character("Sayori", image="sayori")\n'
    'label ch1_main:\n'
    '    scene bg club_day\n'
    '    show sayori 1a at t21\n'
    '    show monika 1b at t22\n'
    '    s "Uno"\n'
)


class FakeList:
    def __init__(self):
        self.sel = ()
        self.items = []

    def insert(self, _i, text):
        self.items.append(text)

    def curselection(self):
        return self.sel

    def selection_clear(self, *a):
        self.sel = ()

    def selection_set(self, i):
        self.sel = (i,)

    def see(self, i):
        pass

    def bind(self, *a, **k):
        pass

    def pack(self, *a, **k):
        pass

    def yview(self, *a):
        pass


tk.Listbox = lambda *a, **k: FakeList()
prtt_gui._photo = lambda *a, **k: None
prtt_gui.ImageTk = types.SimpleNamespace(PhotoImage=lambda im: im)      # sin Tk real: la "foto" es la propia imagen

with tempfile.TemporaryDirectory() as game3:
    for f in ("transforms.rpy", "definitions.rpy"):
        shutil.copy(os.path.join(HERE, f), game3)
    open(os.path.join(game3, "script.rpy"), "w", encoding="utf-8").write(SCRIPT3)
    db3 = rpy_parser.load(game3)
    b3 = prtt_gui.DialogueBrowser(db3, game3)
    book3 = SpriteBook(db3, game3)
    cat3 = cells.SpriteCatalog(db3, book3)
    import preview as _preview
    eng3 = _preview.Preview(db3, game3, book3)
    known3 = set(db3.transform_defs)

    dlg = prtt_gui._PosePicker(b3, cat3, [], current=cells.Placement("sayori", ("1a",)), default_transform="t21",
                               known=known3, title="Celda t21", db=db3, engine=eng3, count=2, slot=1, bg="bg club_day")
    labels = dlg.fx_list.items
    assert "hop focus" in labels and "left in" in labels and "thide" in labels, labels
    assert not [l for l in labels if l[:1] in "tihfsdlr" and l.split()[0][1:].isdigit()], labels   # nada de t11, t22...
    assert dlg.fx_list.sel == (labels.index("tcommon"),), dlg.fx_list.sel                          # t21 = tcommon seleccionado
    # elegir un efecto escribe la variante de ESA celda (hop, celda 1 de 2 -> h21)
    dlg.fx_list.sel = (labels.index("hop"),)
    dlg._on_effect()
    assert dlg.tf.get() == "h21" and "at h21" in dlg.fx_help.config.call_args.kwargs["text"]
    dlg.fx_list.sel = (labels.index("hop focus"),)
    dlg._on_effect()
    assert dlg.tf.get() == "hf21"
    dlg.fx_list.sel = (labels.index("panic"),)
    dlg._on_effect()
    assert dlg.tf.get() == "panic"
    # modo avanzado: texto libre con llamadas; la lista deja de marcar un efecto y el código se actualiza
    dlg.tf.set("t21, hop(x=400, z=0.9)")
    assert dlg.fx_list.sel == () and dlg._check_transform() == ["t21", "hop(x=400, z=0.9)"]
    assert dlg.code.insert.call_args.args[1] == "show sayori 1a zorder 0 at t21, hop(x=400, z=0.9)"
    # al escribir exactamente el de un efecto, la lista lo vuelve a marcar
    dlg.tf.set("hf21")
    assert dlg.fx_list.sel == (labels.index("hop focus"),)
    # un texto imposible no deja aceptar
    dlg.tf.set("hop(")
    dlg.pose, dlg.expr = "1", "a"
    dlg._ok()
    assert dlg.result is None
    dlg.tf.set("t21, hop(x=400, z=0.9)")
    dlg._ok()
    assert dlg.result is not None and dlg.transform == "t21, hop(x=400, z=0.9)" and dlg.reflow is True
    dlg2 = prtt_gui._PosePicker(b3, cat3, [], current=cells.Placement("sayori", ("1a",)), default_transform="t21",
                                known=known3, db=db3, engine=eng3, count=2, slot=1)
    dlg2.tf.set("hf21")
    dlg2.pose, dlg2.expr = "1", "a"
    dlg2._ok()
    assert dlg2.transform == "hf21"

    # ---- ventana de animación
    dlg2.tf.set("hop")
    win = dlg2._pvw = None
    dlg2._open_preview()
    win = dlg2._pvw
    assert isinstance(win, prtt_gui._TransformPreview)
    assert win._span == 0.2                                               # hop dura 0.2 s
    win._draw(0.1)
    assert win._photo.size == (640, 360)
    mid_bytes = win._photo.tobytes()
    win._draw(None)
    assert win._photo.tobytes() != mid_bytes                              # a mitad del salto no está en reposo
    # el evento cambia la duración; panic es un bucle infinito (se enseñan 4 s)
    win.event.set("hide")
    win.play()
    assert win._span == 0.0
    win.event.set("show")
    dlg2.tf.set("panic")
    assert win._span == win.LOOP_SECONDS and "Bucle infinito" in win.info.config.call_args.kwargs["text"]
    # un transform que no está en tu transforms.rpy se avisa; uno imposible no revienta
    dlg2.tf.set("no_existe")
    win._draw(0.0)
    assert "no_existe" in win.info.config.call_args.kwargs["text"]
    dlg2.tf.set("hop(")
    win.play()
    assert "paréntesis" in win.info.config.call_args.kwargs["text"] or "no válido" in win.info.config.call_args.kwargs["text"]
    # reproducción: el temporizador avanza hasta el final, repite si hay bucle y se puede parar
    dlg2.tf.set("hf21")
    win.loop.set(True)
    win._t0 -= 0.0
    win._tick()
    assert win._job is not None
    win._t0 -= 10
    win._tick()
    assert win._job == ("job", 600)                                       # terminó: reinicia (bucle)
    win.stop()
    assert win._job is None
    win.close()
    assert win.destroyed
    # arrastrar la barra de tiempo = ir a ese instante
    dlg2._pvw = None
    dlg2._open_preview()
    win = dlg2._pvw
    win._on_scale("0.05")
    assert win._job is None and win._photo is not None
    # el menú «Previsualizar su animación…» abre la ventana con el transform actual del personaje
    b3._pv_frames = b3._timeline("ch1_main")
    b3._pv_i = 0
    b3._pv = eng3
    made = []
    prtt_gui._TransformPreview = lambda *a, **k: made.append(a)
    b3._pv_open_tf_preview(1)
    assert made and made[0][4]() == "t21" and made[0][3]() == ("1a",) and made[0][5] == 2 and made[0][6] == 1
print("OK")
