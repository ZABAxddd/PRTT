"""Selector de escenarios SIN pantalla (tkinter simulado) con archivos .rpy reales: la ventana, los botones
Reemplazar / Agregar / Eliminar / Deshacer y su conexión con prtt_gui (releer, redibujar)."""
import importlib.util
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock


class FakeModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock(name=name)


class FakeVar:
    def __init__(self, value=None):
        self.v, self.cbs = value, []

    def get(self):
        return self.v

    def set(self, v):
        self.v = v
        for cb in list(self.cbs):
            cb()

    def trace_add(self, _m, cb):
        self.cbs.append(lambda: cb())


class FakeTk:
    def __init__(self, *a, **k):
        self.destroyed = False

    def title(self, t=None):
        pass

    def after(self, ms, fn=None):
        return ("job", ms)

    def after_cancel(self, job):
        pass

    def destroy(self):
        self.destroyed = True

    winfo_rootx = winfo_rooty = lambda self: 0
    winfo_width = lambda self: 1500
    winfo_height = lambda self: 760

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock(name=name)


tk = FakeModule("tkinter")
tk.Tk = tk.Toplevel = tk.Canvas = FakeTk
tk.BooleanVar = tk.IntVar = tk.StringVar = FakeVar
ttk = FakeModule("tkinter.ttk")
ttk.LabelFrame = FakeTk
tk.ttk, tk.messagebox, tk.filedialog, tk.simpledialog, tk.colorchooser = ttk, MagicMock(), MagicMock(), MagicMock(), MagicMock()
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": tk.messagebox,
                    "tkinter.filedialog": tk.filedialog, "tkinter.simpledialog": tk.simpledialog,
                    "tkinter.colorchooser": tk.colorchooser, "tfcatalog": MagicMock()})
sys.modules.setdefault("PIL.ImageTk", types.ModuleType("PIL.ImageTk"))
sys.modules["PIL.ImageTk"].PhotoImage = lambda img: ("foto", img.size)

import rpy_parser  # noqa: E402
import scene_gui  # noqa: E402
import scenes  # noqa: E402
from scene_edit import KEEP  # noqa: E402

spec = importlib.util.spec_from_file_location("prtt_gui", os.path.join(os.path.dirname(os.path.abspath(__file__)), "prtt_gui.py"))
prtt_gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prtt_gui)

SCRIPT = (
    'define s = Character("Sayori", image="sayori")\n'
    'label ch1_main:\n'
    '    scene bg club_day with dissolve\n'
    '    show sayori 1a at t11\n'
    '    s "Uno"\n'
    '    scene bg class_day\n'
    '    with wipeleft\n'
    '    show sayori 1a at t11\n'
    '    s "Dos"\n'
)
DEFS = 'image sayori 1a = "#000000"\nimage bg club_day = "#112233"\nimage bg class_day = "#223344"\nimage bg rojo = "#ff0000"\n'


def rd(p):
    return open(p, encoding="utf-8").read()


with tempfile.TemporaryDirectory() as game:
    sp, dp = os.path.join(game, "script.rpy"), os.path.join(game, "definitions.rpy")
    open(sp, "w", encoding="utf-8").write(SCRIPT)
    open(dp, "w", encoding="utf-8").write(DEFS)
    orig_s, orig_d = rd(sp), rd(dp)
    db = rpy_parser.load(game)
    b = prtt_gui.DialogueBrowser(db, game)
    b._pv_visible = lambda: False
    b._pv_show = MagicMock()
    eng = b._pv_engine()
    assert eng is not None, b._pv_status.config.call_args

    # sin diálogo elegido: avisa y no abre nada
    b._open_scenes()
    assert tk.messagebox.showinfo.called

    b._pv_frames, b._pv_i = b._timeline("ch1_main"), 1               # "Dos"
    holder = []
    b.wait_window = lambda d: holder.append(d)
    b._open_scenes()
    dlg = holder[0]
    assert b._modal == 0 and b._scene_key is None                    # los contadores vuelven a su sitio al cerrar
    b._scene_key = ("ch1_main", 1)                                   # (en la app real la ventana sigue abierta aquí)
    ctx = dlg.ctx
    assert ctx.current.name == "bg class_day" and ctx.previous.name == "bg club_day" and ctx.current.transition == "wipeleft"
    assert dlg.model.current.name == "bg class_day"                  # el carrusel arranca en el fondo actual
    names = [x.name for x in dlg.model.view]
    assert names[:3] == ["bg club_day", "bg class_day", "bg rojo"], names
    assert dlg.v_tr.get() == scene_gui.KEEP_LABEL and dlg._effect() is KEEP
    assert dlg._spec().name == "wipeleft"
    assert dlg.cb_tr.config.call_args.kwargs["values"][0] == scene_gui.KEEP_LABEL

    # --- carrusel (circular) y filtro
    dlg._step(1)
    assert dlg.model.current.name == "bg rojo"
    dlg._step(1)
    assert dlg.model.current.name == "bg club_day" or dlg.model.current.name == "black"
    dlg.v_query.set("roj")
    assert [x.name for x in dlg.model.view] == ["bg rojo"] and dlg.model.current.name == "bg rojo"
    dlg.v_query.set("")
    dlg.model.select("bg rojo")

    # --- elegir transición y ver el efecto: cada fotograma se dibuja a tamaño de vista previa
    dlg.v_tr.set("fade")
    assert dlg._effect() == "fade" and dlg._spec().kind == "fade"
    dlg._play()
    dlg._t0 -= 0.9                                                    # a mitad del efecto
    dlg._tick()
    assert dlg._photos["tr"][1] == scene_gui.PREV
    dlg.v_tr.set("(ninguna)")
    assert dlg._effect() is None and dlg._spec() is None

    # --- REEMPLAZAR: bg rojo con fade; el .rpy cambia, los scripts se releen y el preview se redibuja
    dlg.v_tr.set("fade")
    dlg._do("replace")
    t = rd(sp)
    assert "    scene bg rojo\n    with fade\n" in t, t
    assert b._timeline("ch1_main")[1].stage.bg == "bg rojo"
    assert dlg.ctx.current.name == "bg rojo" and dlg.ctx.previous.name == "bg club_day"
    assert dlg.lbl_status.config.call_args.kwargs["foreground"] == "#1a6b1a"
    assert b._pv_dirty                                                # pestaña Preview no visible: se redibuja al volver

    # --- DESHACER
    dlg._undo()
    assert rd(sp) == orig_s and dlg.ctx.current.name == "bg class_day" and b._timeline("ch1_main")[1].stage.bg == "bg class_day"

    # --- COLOR SÓLIDO nuevo + AGREGAR: escribe la definición en definitions.rpy y el scene en el guion
    tk.colorchooser.askcolor.return_value = ((255, 0, 0), "#12ab34")
    dlg._pick_color()
    cur = dlg.model.current
    assert cur.name == "bg color_12ab34" and cur.new and cur.kind == "color"
    dlg.v_tr.set("dissolve")
    dlg._do("add")
    assert 'image bg rojo = "#ff0000"\nimage bg color_12ab34 = "#12ab34"\n' in rd(dp), rd(dp)
    assert "    scene bg color_12ab34 with dissolve\n    show sayori 1a at t11\n    s \"Dos\"" in rd(sp), rd(sp)
    assert b._timeline("ch1_main")[1].stage.bg == "bg color_12ab34"
    assert "bg color_12ab34" in b.db.images                                         # se releyó todo: ya está definido
    assert not any(x.new for x in dlg.model.items) and dlg.model.current.name == "bg color_12ab34"
    dlg._undo()
    assert rd(sp) == orig_s and rd(dp) == orig_d                                    # los DOS archivos vuelven a como estaban

    # --- ELIMINAR (pide confirmación): vuelve a mandar la escena anterior
    dlg.model.select("bg class_day")
    tk.messagebox.askyesno.return_value = False
    dlg._do("delete")
    assert rd(sp) == orig_s                                                        # cancelar no toca nada
    tk.messagebox.askyesno.return_value = True
    dlg._do("delete")
    assert "class_day" not in rd(sp).split("label")[1] and "wipeleft" not in rd(sp)
    assert b._timeline("ch1_main")[1].stage.bg == "bg club_day"
    assert dlg.ctx.current.name == "bg club_day" and dlg.ctx.previous is None
    assert "Vuelve a mandar" in dlg.lbl_status.config.call_args.kwargs["text"]
    dlg._undo()
    assert rd(sp) == orig_s

    # --- archivo cambiado por fuera: no escribe y lo dice
    open(sp, "a", encoding="utf-8").write("# fuera\n")
    os.utime(sp, ns=(7, 7))
    dlg.model.select("bg rojo")
    dlg._do("replace")
    assert "cambió en disco" in dlg.lbl_status.config.call_args.kwargs["text"]
    assert dlg.lbl_status.config.call_args.kwargs["foreground"] == "#a01010"
    assert "bg rojo" not in rd(sp).split("label")[1]
    # ...«Recargar .rpy» y ya funciona
    b._reload_db(full=True)
    dlg.ctx = dlg._get_context(False)
    dlg._reload_items("bg rojo")
    dlg._do("replace")
    assert "scene bg rojo" in rd(sp)

    # --- sin ninguna escena en el label: eliminar/reemplazar apagados, agregar disponible
    open(sp, "w", encoding="utf-8").write(SCRIPT.replace("    scene bg club_day with dissolve\n", "").replace(
        "    scene bg class_day\n    with wipeleft\n", ""))
    b._reload_db(full=True)
    b._pv_frames, b._pv_i = b._timeline("ch1_main"), 0
    holder.clear()
    b._open_scenes()
    d2 = holder[0]
    b._scene_key = ("ch1_main", 0)
    assert d2.ctx.current is None and "AGREGAR" in " ".join(d2.ctx.notes)
    d2.b_replace.state.assert_called_with(["disabled"])
    d2.b_delete.state.assert_called_with(["disabled"])
    d2.b_add.state.assert_called_with(["!disabled"])
    d2.model.select("bg rojo")
    d2.v_tr.set("dissolve")
    d2._do("add")
    assert "    scene bg rojo with dissolve\n    show sayori 1a at t11\n    s \"Uno\"" in rd(sp), rd(sp)
    assert b._timeline("ch1_main")[0].stage.bg == "bg rojo"
print("OK")
