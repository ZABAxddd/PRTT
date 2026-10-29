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
        self.master = a[0] if a else None

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
    winfo_reqwidth = lambda self: 300
    winfo_reqheight = lambda self: 200

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock(name=name)


tk = FakeModule("tkinter")
tk.Tk = tk.Toplevel = tk.Canvas = FakeTk
tk.BooleanVar = tk.IntVar = tk.StringVar = FakeVar
ttk = FakeModule("tkinter.ttk")
ttk.Frame = ttk.Scrollbar = ttk.LabelFrame = FakeTk
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
    assert dlg._carousel_row.master is dlg._bg_content
    assert dlg._bg_content.master is dlg._scroll_bg.canvas
    resize_calls = []
    dlg._scroll_bg.canvas.itemconfigure = lambda *args, **kwargs: resize_calls.append((args, kwargs))
    dlg._scroll_bg._resize_content(SimpleNamespace(width=800, height=500))
    assert resize_calls[-1][1] == {"width": 800, "height": 500}
    dlg._scroll_bg.canvas.winfo_width = lambda: 800
    dlg._scroll_bg.canvas.winfo_height = lambda: 180
    dlg._bg_content.winfo_reqwidth = lambda: 300
    dlg._bg_content.winfo_reqheight = lambda: 240
    dlg._scroll_bg._content_changed()
    assert resize_calls[-1][1] == {"width": 800, "height": 240}
    names = [x.name for x in dlg.model.view]
    assert names[:3] == ["bg club_day", "bg class_day", "bg rojo"], names
    assert dlg.v_tr.get() == scene_gui.KEEP_LABEL and dlg._effect() is KEEP
    assert dlg._spec().name == "wipeleft"
    current_card = dlg._transition_cards["wipeleft"]
    for card in dlg._transition_cards.values():
        card["label"].configure = MagicMock()
        card["frame"].configure = MagicMock()
    dlg._update_transition_card_selection()
    assert "★ Actual" in current_card["label"].configure.call_args.kwargs["text"]
    assert current_card["frame"].configure.call_args.kwargs == {
        "highlightthickness": 2, "highlightbackground": "#d28b00"
    }
    assert dlg._transition_cards[scene_gui.KEEP_LABEL]["frame"].configure.call_args.kwargs == {
        "highlightthickness": 3, "highlightbackground": "#2376b5"
    }
    assert dlg._transition_options[0] == scene_gui.KEEP_LABEL
    for size in ((120, 68), scene_gui.TRANSITION_CARD, (480, 270)):
        old_image = scene_gui.Image.new("RGBA", size, (0, 0, 0, 255))
        new_image = scene_gui.Image.new("RGBA", size, (255, 255, 255, 255))
        for progress in (0.1, 0.25, 0.5, 0.75, 0.9):
            frame = scene_gui.tr.render(scene_gui.tr.STANDARD["squares"], old_image, new_image, progress)
            assert frame.size == size
    displayed = dlg.cb_tr.config.call_args.kwargs["values"]
    assert displayed[0].startswith(scene_gui.KEEP_LABEL)
    assert "dissolve — with dissolve" in displayed
    assert "wipeleft — with wipeleft" in displayed
    assert set(dlg._transition_cards) == set(dlg._transition_options)
    assert dlg._transition_column_count(410) == 3
    assert dlg._transition_column_count(504) == 4
    assert dlg._transition_column_count(120) == 1
    assert dlg._gallery_transition_name("wipeleft") == "with wipeleft"
    dlg._layout_transition_gallery(SimpleNamespace(width=504))
    sample_card = list(dlg._transition_cards.values())[5]["frame"]
    assert sample_card.grid.call_args.kwargs["row"] == 1
    assert sample_card.grid.call_args.kwargs["column"] == 1
    play_after_card = dlg._play
    dlg._play = MagicMock()
    dlg._select_transition_card("wipeleft")
    assert dlg.v_tr.get() == "wipeleft" and dlg._play.called
    assert dlg.v_tr_choice.get().startswith("wipeleft")
    assert dlg._transition_cards["wipeleft"]["label"].configure.call_args.kwargs["text"].startswith("✓ ★ Actual")
    dlg._play = play_after_card
    dlg.v_tr_choice.set("fade — with fade")
    dlg._on_transition_selected()
    assert dlg.v_tr.get() == "fade"
    assert dlg._transition_cards["fade"]["frame"].configure.call_args.kwargs == {
        "highlightthickness": 3, "highlightbackground": "#2376b5"
    }
    dlg.v_tr.set(scene_gui.KEEP_LABEL)
    dlg._play()
    assert {"pushleft", "pushright", "pushup", "pushdown", "easeout", "easeoutleft",
            "easeoutright", "easeouttop", "easeoutbottom"}.issubset({s.name for s in dlg.ctx.transitions})

    # Las flechas de «Transiciones» recorren efectos y no modifican el fondo elegido.
    selected_bg = dlg.model.current.name
    dlg._step_transition(1)
    assert dlg.v_tr.get() == "(ninguna)" and dlg.model.current.name == selected_bg
    assert dlg.v_tr_choice.get().startswith("(ninguna)")
    assert dlg._transition_cards["(ninguna)"]["frame"].configure.call_args.kwargs == {
        "highlightthickness": 3, "highlightbackground": "#2376b5"
    }
    dlg._step_transition(1)
    assert dlg.v_tr.get() == "dissolve" and dlg.model.current.name == selected_bg
    assert dlg.v_tr_choice.get().startswith("dissolve")
    assert dlg._transition_cards["dissolve"]["frame"].configure.call_args.kwargs == {
        "highlightthickness": 3, "highlightbackground": "#2376b5"
    }
    dlg.v_tr.set(scene_gui.KEEP_LABEL)

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
    gallery_draws = []
    for card in dlg._transition_cards.values():
        card["canvas"].create_image = MagicMock()
        gallery_draws.append(card["canvas"].create_image)
    dlg._play()
    assert set(dlg._transition_gallery_photos) == set(dlg._transition_options)
    assert all(draw.called for draw in gallery_draws)
    dlg._on_transition_card_enter("fade")
    assert dlg._transition_gallery_hover_key == "fade"
    assert dlg._transition_gallery_hover_job is not None
    dlg._tick_transition_gallery_hover("fade")
    assert dlg._transition_gallery_hover_photos["fade"] is not None
    dlg._stop_transition_gallery_hover(render_static=True)
    assert dlg._transition_gallery_hover_key is None
    assert dlg._transition_gallery_hover_job is None
    assert "cv_tr_mini" not in dlg.__dict__
    assert "lbl_tr_mini" not in dlg.__dict__
    dlg._t0 -= 0.9                                                    # a mitad del efecto
    dlg._tick()
    assert dlg._photos["tr"][1] == dlg._preview_size
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
    d2.winfo_width = lambda: 700
    d2.winfo_height = lambda: 400
    d2._adapt_layout()
    assert d2._ui_scale == 0.65
    assert d2._photos["main"][1] == (int(scene_gui.MAIN[0] * 0.65), int(scene_gui.MAIN[1] * 0.65))
assert prtt_gui.POSE_PICKER_MIN_SIZE == (950, 560)
scale_settle = SimpleNamespace(
    _scale_job="pending", _ui_scale=1.0,
    winfo_width=lambda: 950, winfo_height=lambda: 700,
    after_idle=MagicMock(return_value="settle-job"),
    _settle_step2_layout=MagicMock(),
)
prtt_gui._PosePicker._apply_scale(scale_settle)
assert scale_settle.after_idle.call_args.args == (scale_settle._settle_step2_layout,)
assert scale_settle._scale_job is None
assert prtt_gui._SpriteGallery.column_count(121) == 1
assert prtt_gui._SpriteGallery.column_count(244) == 2
assert prtt_gui._SpriteGallery.column_count(366) == 3
assert prtt_gui._SpriteGallery.FACE_THUMB_SIZE == (82, 72)
assert prtt_gui._SpriteGallery.POSE_THUMB_SIZE == (92, 122)
assert prtt_gui._Strip.column_count(510) == 3
assert prtt_gui._Strip.column_count(680) == 4
assert prtt_gui._Strip.column_count(850) == 5
assert prtt_gui._PosePicker._at_effect_column_count(100) == 3
assert prtt_gui._PosePicker._at_effect_column_count(474) == 3
assert prtt_gui._PosePicker._at_effect_column_count(631) == 3
assert prtt_gui._PosePicker._at_effect_column_count(632) == 4
fit_scroller = SimpleNamespace(
    fit_width=True,
    canvas=SimpleNamespace(winfo_width=lambda: 800, winfo_height=lambda: 180,
                           itemconfigure=MagicMock(), configure=MagicMock(), bbox=MagicMock(return_value=(0, 0, 800, 240))),
    content=SimpleNamespace(winfo_reqwidth=lambda: 1200, winfo_reqheight=lambda: 240),
    _window="content",
)
prtt_gui._ScrollableFrame._resize_content(fit_scroller)
assert fit_scroller.canvas.itemconfigure.call_args.kwargs == {"width": 800, "height": 240}

strip = SimpleNamespace(items=[("a", "a"), ("b", "b")], sel="a", page=0, columns=1,
                        refresh=MagicMock())
prtt_gui._Strip.select(strip, "b")
assert strip.sel == "b" and strip.page == 1 and strip.refresh.call_count == 1

picker = SimpleNamespace(
    pose="1", expr="a", strip_face=SimpleNamespace(select=MagicMock()),
    strip_pose=SimpleNamespace(select=MagicMock(), refresh=MagicMock()),
    _reload_faces=MagicMock(), _refresh_sprite_gallery=MagicMock(), _on_tf_tab_changed=MagicMock(),
    _update=MagicMock(),
)
prtt_gui._PosePicker._pick_expr(picker, "b")
assert picker.expr == "b" and picker.strip_face.select.call_args.args == ("b",)
assert picker._on_tf_tab_changed.called
prtt_gui._PosePicker._pick_pose(picker, "2")
assert picker.pose == "2" and picker.strip_pose.select.call_args.args == ("2",)
assert picker._on_tf_tab_changed.call_count == 2

gallery_buttons = []
class GalleryButton:
    def __init__(self, _master, **kwargs):
        self.options = kwargs
        self.bind = MagicMock()
        self.grid = MagicMock()
        gallery_buttons.append(self)

content = SimpleNamespace(winfo_children=lambda: [], columnconfigure=MagicMock())
panel = SimpleNamespace(
    canvas=SimpleNamespace(winfo_width=lambda: 300),
    _wheel=MagicMock(), _resize_content=MagicMock(),
)
gallery = SimpleNamespace(
    _content={"face": content}, _photos={"face": []}, _tiles={"face": []},
    _items={}, _selected={}, _columns={"face": 0}, _panels={"face": panel}, _layout=MagicMock(),
)
original_button = prtt_gui.tk.Button
try:
    prtt_gui.tk.Button = GalleryButton
    prtt_gui._SpriteGallery.refresh(
        gallery, "face", [("a", "a"), ("b", "b")], "b", lambda _key: None
    )
finally:
    prtt_gui.tk.Button = original_button
selected_options = gallery_buttons[1].options
assert selected_options["text"] == "✓ b"
assert selected_options["bg"] == "#ffe680" and selected_options["relief"] == "solid"

hop = SimpleNamespace(name="hop", label="hop", known=True)
custom = SimpleNamespace(name="custom", label="custom", known=False)
at_cards = {
    effect.name: {
        "effect": effect, "frame": SimpleNamespace(configure=MagicMock()),
        "label": SimpleNamespace(configure=MagicMock()),
    }
    for effect in (hop, custom)
}
at_picker = SimpleNamespace(
    tf=SimpleNamespace(get=lambda: "h21"), _db=object(), _effects=[hop, custom],
    _at_gallery_cards=at_cards, _at_effect_text=lambda effect: {
        "hop": "h21", "custom": "custom",
    }[effect.name],
)
original_effect_of = prtt_gui.tfcatalog.effect_of
try:
    prtt_gui.tfcatalog.effect_of = MagicMock(return_value=hop)
    prtt_gui._PosePicker._update_at_effect_selection(at_picker)
finally:
    prtt_gui.tfcatalog.effect_of = original_effect_of
assert at_cards["hop"]["frame"].configure.call_args.kwargs == {
    "highlightthickness": 3, "highlightbackground": "#2376b5"
}
assert at_cards["hop"]["label"].configure.call_args.kwargs["text"] == "✓ hop\nat h21"
assert at_cards["custom"]["frame"].configure.call_args.kwargs == {
    "highlightthickness": 1, "highlightbackground": "#b8b8b8"
}
assert at_cards["custom"]["label"].configure.call_args.kwargs["text"] == "custom   · propio\nat custom"

at_picker.fx_list = SimpleNamespace(
    selection_clear=MagicMock(), selection_set=MagicMock(), see=MagicMock(),
)
at_picker._on_effect = MagicMock()
prtt_gui._PosePicker._select_at_effect_card(at_picker, "custom")
assert at_picker.fx_list.selection_set.call_args.args == (1,)
assert at_picker.fx_list.see.call_args.args == (1,) and at_picker._on_effect.called

effect_choice = SimpleNamespace(
    fx_list=SimpleNamespace(curselection=lambda: (0,)),
    _effects=[hop], _count=2, _slot=1, _defined=lambda: set(),
    _syncing=False, tf=SimpleNamespace(set=MagicMock()),
    _show_effect_help=MagicMock(), _update_at_effect_selection=MagicMock(),
    _reload_at_effect_preview=MagicMock(),
)
original_resolve = prtt_gui.tfcatalog.resolve
try:
    prtt_gui.tfcatalog.resolve = MagicMock(return_value="h21")
    prtt_gui._PosePicker._on_effect(effect_choice)
finally:
    prtt_gui.tfcatalog.resolve = original_resolve
assert effect_choice.tf.set.call_args.args == ("h21",)
assert effect_choice._reload_at_effect_preview.called

preview_image = scene_gui.Image.new("RGB", (1280, 720), "black")
preview_lib = SimpleNamespace(
    duration_expr=MagicMock(return_value=0.4),
    loops_expr=MagicMock(return_value=False),
)
at_preview = SimpleNamespace(
    _engine=SimpleNamespace(lib=preview_lib),
    _at_gallery_context=("sayori", ("1a",), "a", ""),
    _at_gallery_photos={},
    _effects=[hop],
    _at_effect_text=lambda _effect: "h21",
    _at_effect_image=MagicMock(return_value=preview_image),
)
static_photo = prtt_gui._PosePicker._at_effect_static_photo(at_preview, hop)
assert static_photo == ("foto", prtt_gui.AT_EFFECT_CARD_SIZE)
assert at_preview._at_effect_image.call_args.args == ("hop", 0.2)
assert prtt_gui._PosePicker._at_effect_static_photo(at_preview, hop) == static_photo
assert at_preview._at_effect_image.call_count == 1

hover_preview = SimpleNamespace(
    _engine=SimpleNamespace(lib=preview_lib),
    _at_gallery_cards={"hop": {"effect": hop}},
    _at_effect_text=lambda _effect: "h21",
    _at_effect_image=MagicMock(return_value=preview_image),
    _at_gallery_hover_photo=None,
    _draw_at_effect_card=MagicMock(),
)
prtt_gui._PosePicker._render_at_effect_hover(hover_preview, "hop", 0.1)
assert hover_preview._at_effect_image.call_args.args == ("hop", 0.1)
assert hover_preview._draw_at_effect_card.call_args.args == (
    "hop", ("foto", prtt_gui.AT_EFFECT_CARD_SIZE),
)

effect_preview = SimpleNamespace(
    _at_preview_job="pending", tfnb=SimpleNamespace(select=lambda: "effects"),
    effects_page="effects", _at_preview_started=0.0, _at_preview_duration=0.4,
    _at_preview_loops=False, _at_preview_finished=False,
    _draw_at_effect_preview=MagicMock(), after=MagicMock(return_value="next"),
)
original_monotonic = prtt_gui.time.monotonic
try:
    prtt_gui.time.monotonic = lambda: 1.0
    prtt_gui._PosePicker._tick_at_effect_preview(effect_preview)
finally:
    prtt_gui.time.monotonic = original_monotonic
assert effect_preview._draw_at_effect_preview.call_args.args == (None,)
assert effect_preview._at_preview_finished and effect_preview.after.call_count == 0

loop_preview = SimpleNamespace(
    _at_preview_job="pending", tfnb=SimpleNamespace(select=lambda: "effects"),
    effects_page="effects", _at_preview_started=0.0, _at_preview_duration=0.4,
    _at_preview_loops=True, _at_preview_finished=False,
    _draw_at_effect_preview=MagicMock(), after=MagicMock(return_value="next"),
    _tick_at_effect_preview=MagicMock(),
)
try:
    prtt_gui.time.monotonic = lambda: 5.0
    prtt_gui._PosePicker._tick_at_effect_preview(loop_preview)
finally:
    prtt_gui.time.monotonic = original_monotonic
assert abs(loop_preview._draw_at_effect_preview.call_args.args[0] - 0.2) < 0.001
assert not loop_preview._at_preview_finished and loop_preview.after.call_count == 1

loading_preview = SimpleNamespace(
    _at_gallery_loading=True, tfnb=SimpleNamespace(select=lambda: "effects"),
    effects_page="effects", tag=SimpleNamespace(get=lambda: "sayori"),
    _engine=SimpleNamespace(lib=SimpleNamespace(
        duration_expr=MagicMock(return_value=0.4), loops_expr=MagicMock(return_value=False),
    )),
    tf=SimpleNamespace(get=lambda: "h21"),
    _at_effect_context=lambda: ("sayori", ("1a",), "a", ""),
    _at_preview_expr=None, _at_preview_job=None, _at_preview_loop_var=SimpleNamespace(get=lambda: False),
    _stop_at_effect_preview=MagicMock(), _tick_at_effect_preview=MagicMock(),
)
prtt_gui._PosePicker._refresh_at_effect_preview(loading_preview)
assert loading_preview._tick_at_effect_preview.called

resize_preview = SimpleNamespace(
    _ui_scale=1.0, _at_preview_label=SimpleNamespace(config=MagicMock()),
)
preview_image = scene_gui.Image.new("RGB", (1280, 720), "black")
prtt_gui._PosePicker._show_at_effect_preview_image(resize_preview, preview_image)
assert resize_preview._at_preview_photo == ("foto", (360, 202))

cache_effect = SimpleNamespace(name="hop", label="hop")
cache_loader = SimpleNamespace(
    _at_gallery_load_job="queued", _at_gallery_load_token=7, _at_gallery_loading=True,
    _at_gallery_load_index=0, _effects=[cache_effect], _at_gallery_status=SimpleNamespace(config=MagicMock()),
    _at_gallery_detail=SimpleNamespace(config=MagicMock()),
    _at_gallery_progress=SimpleNamespace(configure=MagicMock()),
    _at_gallery_joke_shown=False,
    _at_effect_static_photo=MagicMock(return_value="cached-photo"),
    _draw_at_effect_card=MagicMock(), after=MagicMock(return_value="next-job"),
)
prtt_gui._PosePicker._cache_next_at_effect(cache_loader, 7)
assert cache_loader._at_gallery_status.config.call_args.kwargs["text"] == "Cargando previews: 1 de 1"
assert cache_loader._at_gallery_detail.config.call_args.kwargs["text"] == "Esperar a que Monika revise el script"
assert cache_loader._at_gallery_progress.configure.call_args.kwargs["value"] == 1
assert cache_loader.after.call_args.args[0] == 500
assert cache_loader._at_gallery_load_job == "next-job"
assert abs(
    prtt_gui.AT_EFFECT_PREVIEW_SIZE[0] / prtt_gui.AT_EFFECT_PREVIEW_SIZE[1] - 16 / 9
) < 0.01
assert not hasattr(prtt_gui, "_TransformPreview")
print("OK")
