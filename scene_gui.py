"""
scene_gui.py - Ventana «Escenarios»: carrusel de fondos, color sólido, transición con vista previa y los botones
Reemplazar / Agregar / Eliminar la instrucción `scene` del diálogo elegido.

Toda la lógica está en scenes.py (fondos y carrusel), transitions.py (efectos) y scene_edit.py
(escritura en los .rpy); esta ventana solo la dibuja. Se abre desde el botón «🎬 Escenarios…» de PRTT.

La ventana recibe, en vez de datos, funciones del programa principal:
    get_engine()                     -> preview.Preview (dibuja los fondos con tus mismas imágenes) o None
    get_context(include_all)         -> scene_edit.Context (qué escena manda ahora, fondos, transiciones)
    do_apply(op, bg, effect, reshow) -> (ok, mensaje)   op = "replace" | "add" | "delete"
    do_undo()                        -> (ok, mensaje)
    can_undo()                       -> bool
"""
import time
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

import scene_edit
import scenes
import transitions as tr
from ui_layout import fit_window

try:
    from PIL import Image, ImageTk
    HAVE_TKIMG = True
except ImportError:                                   # pragma: no cover
    HAVE_TKIMG = False

MAIN = (512, 288)          # fondo grande del carrusel
SIDE = (160, 90)           # los dos de los lados
PREV = (480, 270)          # vista previa de la transición (pestaña «Transiciones»)
TRANSITION_CARD = (112, 63)
TRANSITION_CARD_MIN_WIDTH = 126
HOLD_START, HOLD_END = 0.35, 0.7      # segundos que se ve el fondo viejo antes y el nuevo después
KEEP_LABEL = "(conservar la actual)"


class _ScrollableFrame(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hscroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.content = ttk.Frame(self.canvas, **kwargs)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._resize_content)
        self.canvas.configure(yscrollcommand=self._set_vertical_scroll,
                              xscrollcommand=self._set_horizontal_scroll)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.focus_set())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._wheel_horizontal)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")

    def _content_changed(self, _event=None) -> None:
        self._resize_content()

    def _resize_content(self, event=None) -> None:
        width = event.width if event is not None else self.canvas.winfo_width()
        height = event.height if event is not None else self.canvas.winfo_height()
        self.canvas.itemconfigure(
            self._window,
            width=max(width, self.content.winfo_reqwidth()),
            height=max(height, self.content.winfo_reqheight()),
        )
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _set_vertical_scroll(self, first, last) -> None:
        self.vscroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            if self.vscroll.winfo_manager():
                self.vscroll.grid_remove()
        elif self.vscroll.winfo_manager() != "grid":
            self.vscroll.grid(row=0, column=1, sticky="ns")

    def _set_horizontal_scroll(self, first, last) -> None:
        self.hscroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            if self.hscroll.winfo_manager():
                self.hscroll.grid_remove()
        elif self.hscroll.winfo_manager() != "grid":
            self.hscroll.grid(row=1, column=0, sticky="ew")

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _wheel_horizontal(self, event) -> None:
        self.canvas.xview_scroll(-int(event.delta / 120), "units")


class SceneBrowser(tk.Toplevel):
    def __init__(self, master, get_engine, get_context, do_apply, do_undo, can_undo):
        super().__init__(master)
        self.title("Escenarios · fondos y transiciones")
        self.resizable(True, True)
        width, height = 1040, 580
        self.geometry(f"{width}x{height}")
        self._get_engine, self._get_context = get_engine, get_context
        self._do_apply, self._do_undo, self._can_undo = do_apply, do_undo, can_undo
        self._cache = {}              # (nombre, tamaño) -> imagen PIL RGB
        self._warns = {}              # nombre -> avisos del motor al dibujarlo
        self._photos = {}             # referencias a las PhotoImage (Tk las borra si no se guardan)
        self._new_colors = []         # colores creados aquí y aún sin escribir en los .rpy
        self._job = None
        self._t0 = 0.0
        self._ui_scale = 1.0
        self._preview_size = None
        self._preview_resize_job = None
        self._transition_cards = {}
        self._transition_gallery_columns = 0
        self._transition_gallery_photos = {}
        self._transition_gallery_hover_photos = {}
        self._transition_gallery_pair = None
        self._transition_gallery_images = None
        self._transition_gallery_hover_key = None
        self._transition_gallery_hover_job = None
        self._transition_gallery_leave_job = None
        self._transition_gallery_hover_started = 0.0
        self._old = self._new = None
        self.ctx = get_context(False)
        self.model = scenes.SceneModel(list(self.ctx.backgrounds))
        self.v_all = tk.BooleanVar(value=False)
        self.v_loop = tk.BooleanVar(value=True)
        self.v_reshow = tk.BooleanVar(value=True)
        self.v_query = tk.StringVar()
        self.v_tr = tk.StringVar()
        self._build()
        self.model.select(self.ctx.current_bg)
        self._fill_transitions()
        self.bind("<Left>", lambda e: self._key(-1))
        self.bind("<Right>", lambda e: self._key(1))
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Configure>", self._schedule_adapt)
        self.update_idletasks()
        width, height = fit_window(
            self, width, height, parent=master,
            min_size=(min(800, width), min(480, height)),
        )
        self.minsize(min(800, width), min(480, height))
        self._adapt_layout(refresh=False)
        self._refresh()
        self._fit_initial_height(master)
        self.grab_set()

    # ------------------------------------------------------------------ interfaz
    def _build(self) -> None:
        top = ttk.Frame(self, padding=(10, 8, 10, 2))
        top.pack(fill="x")
        self.lbl_where = ttk.Label(top, font=("TkDefaultFont", 10, "bold"))
        self.lbl_where.pack(anchor="w")
        self.lbl_cur = ttk.Label(top, foreground="#333333", wraplength=980, justify="left")
        self.lbl_cur.pack(anchor="w")
        self.lbl_prev = ttk.Label(top, foreground="#777777", wraplength=980, justify="left")
        self.lbl_prev.pack(anchor="w")

        # Dos pestañas en la misma ventana: los fondos (carrusel) y las transiciones (con su vista previa).
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=6)
        left_page = ttk.Frame(self.nb)
        right_page = ttk.Frame(self.nb)
        left = _ScrollableFrame(left_page, padding=8)
        right = _ScrollableFrame(right_page, padding=8)
        self._scroll_bg, self._scroll_tr = left, right
        bg_content, tr_content = left.content, right.content
        self._bg_content, self._tr_content = bg_content, tr_content
        left.pack(fill="both", expand=True)
        right.pack(fill="both", expand=True)
        self.tab_bg, self.tab_tr = left_page, right_page
        self.nb.add(left_page, text="  Fondos  ")
        self.nb.add(right_page, text="  Transiciones  ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab())

        # --- carrusel: anterior · ACTUAL · siguiente
        row = ttk.Frame(bg_content)
        self._carousel_row = row
        row.pack(anchor="center", pady=(4, 0))
        ttk.Button(row, text="◀", width=3, command=lambda: self._step(-1)).grid(row=0, column=0, padx=(0, 6))
        self.cv_prev = tk.Canvas(row, width=SIDE[0], height=SIDE[1], highlightthickness=1, bg="#202020", cursor="hand2")
        self.cv_main = tk.Canvas(row, width=MAIN[0], height=MAIN[1], highlightthickness=2, bg="#202020")
        self.cv_next = tk.Canvas(row, width=SIDE[0], height=SIDE[1], highlightthickness=1, bg="#202020", cursor="hand2")
        self.cv_prev.grid(row=0, column=1, padx=4)
        self.cv_main.grid(row=0, column=2, padx=4)
        self.cv_next.grid(row=0, column=3, padx=4)
        ttk.Button(row, text="▶", width=3, command=lambda: self._step(1)).grid(row=0, column=4, padx=(6, 0))
        self.cv_prev.bind("<Button-1>", lambda e: self._step(-1))
        self.cv_next.bind("<Button-1>", lambda e: self._step(1))
        self.lbl_name = ttk.Label(bg_content, font=("TkDefaultFont", 11, "bold"))
        self.lbl_name.pack(pady=(8, 0))
        self.lbl_origin = ttk.Label(bg_content, foreground="#666666")
        self.lbl_origin.pack()
        self.lbl_warn = ttk.Label(bg_content, foreground="#a05000", wraplength=560, justify="center")
        self.lbl_warn.pack()

        flt = ttk.Frame(bg_content)
        flt.pack(fill="x", pady=(10, 0))
        ttk.Label(flt, text="Filtrar:").pack(side="left")
        ent = ttk.Entry(flt, textvariable=self.v_query, width=22)
        ent.pack(side="left", padx=6)
        self.v_query.trace_add("write", lambda *_: self._on_filter())
        ttk.Checkbutton(flt, text="Mostrar todas las imágenes (no solo bg)", variable=self.v_all,
                        command=self._on_all).pack(side="left", padx=8)
        ttk.Button(flt, text="🎨 Color sólido…", command=self._pick_color).pack(side="right")
        self.lbl_tr_sum = ttk.Label(bg_content, foreground="#555555", wraplength=640, justify="left")
        self.lbl_tr_sum.pack(anchor="w", pady=(10, 0))

        # --- pestaña «Transiciones»
        pick = ttk.Frame(tr_content)
        pick.pack(fill="x")
        ttk.Button(pick, text="◀", width=3, command=lambda: self._step_transition(-1)).pack(side="left")
        self.v_tr_choice = tk.StringVar()
        self.cb_tr = ttk.Combobox(pick, textvariable=self.v_tr_choice, state="readonly", width=48)
        self.cb_tr.pack(side="left", fill="x", expand=True, padx=8)
        self.cb_tr.bind("<<ComboboxSelected>>", self._on_transition_selected)
        ttk.Button(pick, text="▶", width=3, command=lambda: self._step_transition(1)).pack(side="right")
        body = ttk.Frame(tr_content)
        self._tr_body = body
        body.pack(fill="both", expand=True, pady=(6, 0))
        details = ttk.Frame(body, width=410)
        self._tr_details = details
        details.pack(side="right", fill="y", padx=(8, 0))
        details.pack_propagate(False)
        body.bind("<Configure>", self._resize_transition_details)
        preview_panel = ttk.Frame(body)
        preview_panel.pack(side="left", fill="both", expand=True)
        self.lbl_route = ttk.Label(preview_panel, anchor="center", font=("TkDefaultFont", 10, "bold"))
        self.lbl_route.pack(fill="x")
        self.cv_tr = tk.Canvas(preview_panel, width=PREV[0], height=PREV[1], highlightthickness=1, bg="#101010")
        self.cv_tr.pack(fill="both", expand=True, pady=(6, 0))
        self.cv_tr.bind("<Configure>", self._schedule_preview_resize)
        ctl = ttk.Frame(preview_panel)
        ctl.pack(pady=(6, 0))
        ttk.Button(ctl, text="▶ Reproducir", command=self._play).pack(side="left")
        ttk.Checkbutton(ctl, text="Repetir", variable=self.v_loop, command=self._play).pack(side="left", padx=10)

        self.lbl_tr = ttk.Label(details, foreground="#555555", wraplength=380, justify="left")
        self.lbl_tr.pack(anchor="w", pady=(4, 6))
        self.lbl_tr_help = ttk.Label(details, foreground="#777777", wraplength=380, justify="left", text=(
            "Al elegir el fondo actual, la vista previa muestra el fondo anterior y el actual; si no existe uno "
            "anterior, usa gris. Al elegir otro fondo, muestra el actual y el nuevo. Las flechas recorren las "
            "transiciones estándar de Ren'Py y las definidas o usadas por el mod. La transición elegida se usa "
            "al pulsar Reemplazar o Agregar."
        ))
        self.lbl_tr_help.pack(anchor="w", pady=(6, 0))

        ttk.Label(details, text="Previews de transiciones · selecciona una para aplicarla").pack(
            anchor="w", pady=(8, 3))
        self._transition_gallery = _ScrollableFrame(details, padding=3)
        self._transition_gallery.pack(fill="both", expand=True)
        self._transition_gallery.canvas.bind("<Configure>", self._layout_transition_gallery)

        # --- acciones
        acts = ttk.Frame(self, padding=(10, 4, 10, 2))
        acts.pack(fill="x")
        self.b_replace = ttk.Button(acts, text="Reemplazar escena actual", command=lambda: self._do("replace"))
        self.b_add = ttk.Button(acts, text="Agregar nuevo escenario", command=lambda: self._do("add"))
        self.b_delete = ttk.Button(acts, text="Eliminar escenario", command=lambda: self._do("delete"))
        self.b_undo = ttk.Button(acts, text="↶ Deshacer último cambio", command=self._undo)
        for b in (self.b_replace, self.b_add, self.b_delete):
            b.pack(side="left", padx=(0, 6))
        ttk.Button(acts, text="Cerrar", command=self.destroy).pack(side="right")
        self.b_undo.pack(side="right", padx=6)
        opts = ttk.Frame(self, padding=(10, 0, 10, 0))
        opts.pack(fill="x")
        ttk.Checkbutton(opts, variable=self.v_reshow, text=(
            "Al agregar, volver a mostrar a los personajes que hay en escena (un `scene` los borra)")).pack(anchor="w")
        self.lbl_status = ttk.Label(self, padding=(10, 4, 10, 8), wraplength=1000, justify="left")
        self.lbl_status.pack(fill="x")

    def _fill_transitions(self) -> None:
        self._specs = {s.label: s for s in self.ctx.transitions}
        self._transition_options = [KEEP_LABEL] + list(self._specs)
        self._display_to_key = {self._transition_display(key): key for key in self._transition_options}
        self._key_to_display = {key: display for display, key in self._display_to_key.items()}
        self.cb_tr.config(values=list(self._display_to_key))
        self.v_tr.set(KEEP_LABEL if self.ctx.current is not None else tr.NONE_SPEC.label)
        self._show_transition_choice()
        self._build_transition_gallery()

    def _build_transition_gallery(self) -> None:
        self._stop_transition_gallery_hover(render_static=False)
        content = self._transition_gallery.content
        for child in content.winfo_children():
            child.destroy()
        self._transition_cards.clear()
        self._transition_gallery_photos.clear()
        self._transition_gallery_pair = None
        for index, key in enumerate(self._transition_options):
            card = tk.Frame(content, bd=0, highlightthickness=1, highlightbackground="#b8b8b8")
            canvas = tk.Canvas(card, width=TRANSITION_CARD[0], height=TRANSITION_CARD[1],
                               highlightthickness=0, bg="#101010")
            canvas.pack(padx=3, pady=(3, 1))
            label = ttk.Label(card, text=self._gallery_transition_name(key), anchor="center", justify="center",
                              wraplength=TRANSITION_CARD[0])
            label.pack(fill="x", padx=2, pady=(0, 3))
            for widget in (card, canvas, label):
                widget.bind("<Button-1>", lambda _event, selected=key: self._select_transition_card(selected))
                widget.bind("<MouseWheel>", self._transition_gallery._wheel)
                widget.bind("<Enter>", lambda _event, hovered=key: self._on_transition_card_enter(hovered))
                widget.bind("<Leave>", lambda _event, hovered=key: self._on_transition_card_leave(hovered))
            self._transition_cards[key] = {"frame": card, "canvas": canvas, "label": label}
        self._layout_transition_gallery()
        self._update_transition_card_selection()

    def _gallery_transition_name(self, key: str) -> str:
        if key == KEEP_LABEL:
            return "Conservar actual"
        spec = self._specs.get(key)
        if spec is None or spec.kind == "none":
            return "Sin transición\n(ninguna)"
        return f"with {spec.name}"

    def _layout_transition_gallery(self, event=None) -> None:
        if not hasattr(self, "_transition_gallery"):
            return
        content = self._transition_gallery.content
        width = event.width if event is not None else self._transition_gallery.canvas.winfo_width()
        columns = self._transition_column_count(width)
        for column in range(max(columns, self._transition_gallery_columns)):
            content.columnconfigure(
                column,
                weight=1 if column < columns else 0,
                uniform="transition-cards" if column < columns else "",
            )
        self._transition_gallery_columns = columns
        for index, card in enumerate(self._transition_cards.values()):
            card["frame"].grid(row=index // columns, column=index % columns, padx=2, pady=2, sticky="nsew")
        self._transition_gallery._resize_content()

    @staticmethod
    def _transition_column_count(width: int) -> int:
        return max(1, width // TRANSITION_CARD_MIN_WIDTH)

    def _resize_transition_details(self, event=None) -> None:
        if event is None:
            width = self._tr_body.winfo_width()
        else:
            width = event.width
        if width <= 1:
            return
        available = max(1, width - 8)
        details_width = min(available - 300, max(410, int(available * 0.42)))
        details_width = max(1, details_width)
        self._tr_details.configure(width=details_width)
        self.lbl_tr.config(wraplength=max(100, details_width - 24))
        self.lbl_tr_help.config(wraplength=max(100, details_width - 24))

    def _show_transition_choice(self) -> None:
        self.v_tr_choice.set(self._key_to_display.get(self.v_tr.get(), self.v_tr.get()))
        if hasattr(self, "_transition_cards"):
            self._update_transition_card_selection()

    def _transition_display(self, key: str) -> str:
        if key == KEEP_LABEL:
            return f"{key} — mantener la transición existente"
        spec = self._specs.get(key)
        if spec is None:
            return key
        marker = " (aprox.)" if spec.approx else ""
        if spec.source == "mod":
            marker += " · tuya"
        elif spec.source == "usada":
            marker += " · usada en scripts"
        return f"{spec.name}{marker} — with {spec.name}"

    def _on_transition_selected(self, _event=None) -> None:
        key = self._display_to_key.get(self.v_tr_choice.get())
        if key is not None:
            self.v_tr.set(key)
            self._update_transition_card_selection()
            self._play()

    def _select_transition_card(self, key: str) -> None:
        self.v_tr.set(key)
        self._show_transition_choice()
        self._update_transition_card_selection()
        self._play()

    def _update_transition_card_selection(self) -> None:
        selected = self.v_tr.get()
        current_transition = self.ctx.current.transition if self.ctx.current else None
        for key, card in self._transition_cards.items():
            frame = card["frame"]
            spec = self._specs.get(key)
            is_current = bool(current_transition and spec and spec.name == current_transition)
            frame.configure(
                highlightthickness=3 if key == selected else (2 if is_current else 1),
                highlightbackground="#2376b5" if key == selected else (
                    "#d28b00" if is_current else "#b8b8b8"),
            )
            card["label"].configure(
                text=("✓ " if key == selected else "") +
                ("★ Actual · " if is_current else "") +
                self._gallery_transition_name(key),
            )

    def _schedule_adapt(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_adapt_job", None) is not None:
            self.after_cancel(self._adapt_job)
        self._adapt_job = self.after(80, self._adapt_layout)

    def _fit_initial_height(self, master) -> None:
        min_height = min(480, self.winfo_height())
        changed = False
        self.update_idletasks()
        self.update()
        for _ in range(8):
            canvas = self._scroll_bg.canvas
            extra = canvas.winfo_height() - self._bg_content.winfo_reqheight()
            if extra <= 2:
                break
            current_height = self.winfo_height()
            target_height = max(min_height, current_height - extra)
            if target_height >= current_height:
                break
            width, height = fit_window(
                self, self.winfo_width(), target_height, parent=master,
                min_size=(min(800, self.winfo_width()), min_height),
            )
            self.minsize(min(800, width), min(480, height))
            self.update_idletasks()
            self.update()
            self._adapt_layout(refresh=False)
            changed = True
        if changed:
            self.update_idletasks()
            self.update()
            self._adapt_layout(refresh=False)
            self._refresh()

    def _adapt_layout(self, refresh=True) -> None:
        adapt_job = getattr(self, "_adapt_job", None)
        if adapt_job is not None:
            self.after_cancel(adapt_job)
        self._adapt_job = None
        width, height = max(self.winfo_width(), 1), max(self.winfo_height(), 1)
        scale = max(0.65, min(1.0, width / 1040, (height - 150) / 550))
        if abs(scale - self._ui_scale) < 0.005:
            return
        self._ui_scale = scale
        for canvas, size in ((self.cv_prev, SIDE), (self.cv_main, MAIN), (self.cv_next, SIDE)):
            canvas.config(width=max(80, int(size[0] * scale)), height=max(45, int(size[1] * scale)))
        self.cv_tr.config(width=max(240, int(PREV[0] * scale)), height=max(135, int(PREV[1] * scale)))
        self._cache.clear()
        self.update_idletasks()
        self._sync_scroll_content()
        if refresh:
            self._refresh()

    def _scaled_size(self, size):
        return max(1, int(size[0] * self._ui_scale)), max(1, int(size[1] * self._ui_scale))

    def _preview_size_for_canvas(self):
        width, height = self.cv_tr.winfo_width(), self.cv_tr.winfo_height()
        if width < 240 or height < 135:
            return self._scaled_size(PREV)
        base = self._scaled_size(PREV)
        ratio = min(width / base[0], height / base[1], 1280 / base[0])
        return max(1, int(base[0] * ratio)), max(1, int(base[1] * ratio))

    def _schedule_preview_resize(self, _event=None) -> None:
        if self._preview_resize_job is not None:
            self.after_cancel(self._preview_resize_job)
        self._preview_resize_job = self.after(100, self._resize_preview)

    def _resize_preview(self) -> None:
        self._preview_resize_job = None
        size = self._preview_size_for_canvas()
        if size != self._preview_size and self.model.current is not None:
            self._play()

    # ------------------------------------------------------------------ imágenes
    def _bg_for(self, name: str):
        if not name:
            return scenes.Background("black", "color", color="#000000")
        found = next((b for b in self.model.items if b.name == name), None)
        return found or scenes.Background(name, "missing")

    def _image(self, bg, size):
        """Fondo `bg` a tamaño `size` (PIL RGB). Se dibuja con el motor del preview: tus mismas imágenes."""
        key = (bg.name, bg.color, size)
        if key in self._cache:
            return self._cache[key]
        big = None
        if bg.kind == "color" and bg.color:
            big = Image.new("RGB", (1280, 720), bg.color)
        else:
            eng = self._get_engine()
            if eng is not None:
                canvas = Image.new("RGBA", (1280, 720), (0, 0, 0, 255))
                eng._cur = []
                eng._draw_full(canvas, bg.name, "fondo")
                self._warns[bg.name] = "  ·  ".join(eng._cur)
                big = canvas.convert("RGB")
        if big is None:
            big = Image.new("RGB", (1280, 720), (40, 40, 50))
        img = big.resize(size, getattr(Image, "Resampling", Image).LANCZOS)
        self._cache[key] = img
        return img

    def _show(self, canvas, img, tag: str) -> None:
        photo = ImageTk.PhotoImage(img)
        self._photos[tag] = photo
        canvas.delete("all")
        if tag == "tr":
            canvas.create_image(canvas.winfo_width() // 2, canvas.winfo_height() // 2, anchor="center", image=photo)
        else:
            canvas.create_image(0, 0, anchor="nw", image=photo)

    # ------------------------------------------------------------------ carrusel
    def _refresh(self) -> None:
        ctx = self.ctx
        self.lbl_where.config(text=f"Diálogo: {ctx.where}")
        self.lbl_cur.config(text=("Escena que manda aquí:  " + ctx.current.text) if ctx.current
                            else "Escena que manda aquí:  (ninguna en este label)  ·  " + " ".join(ctx.notes))
        self.lbl_prev.config(text=("Escena anterior (la que vuelve si eliminas esta):  " + ctx.previous.text)
                             if ctx.previous else "Escena anterior:  (no hay)")
        bg = self.model.current
        n = len(self.model.view)
        if bg is None:
            for cv in (self.cv_prev, self.cv_main, self.cv_next):
                cv.delete("all")
            self.lbl_name.config(text="(ningún fondo coincide con el filtro)")
            self.lbl_origin.config(text="")
            self.lbl_warn.config(text="")
        elif not HAVE_TKIMG:
            self.lbl_name.config(text=bg.name)
            self.lbl_origin.config(text="Falta Pillow para ver los fondos:  py -3 -m pip install pillow")
        else:
            self._show(self.cv_main, self._image(bg, self._scaled_size(MAIN)), "main")
            for cv, d, tag in ((self.cv_prev, -1, "prev"), (self.cv_next, 1, "next")):
                nb = self.model.neighbor(d)
                if nb is None:
                    cv.delete("all")
                else:
                    self._show(cv, self._image(nb, self._scaled_size(SIDE)), tag)
            mark = "  ★ el actual" if bg.name == ctx.current_bg else ""
            self.lbl_name.config(text=f"{bg.name}{mark}   ({self.model.index + 1} / {n})")
            self.lbl_origin.config(text=f"{bg.kind} · {bg.origin}")
            self.lbl_warn.config(text=self._warns.get(bg.name, ""))
        has_bg = bg is not None
        self._enable(self.b_replace, has_bg and ctx.current is not None)
        self._enable(self.b_add, has_bg)
        self._enable(self.b_delete, ctx.current is not None)
        self._enable(self.b_undo, bool(self._can_undo()))
        self._sync_tr()
        self._sync_scroll_content()

    def _sync_scroll_content(self) -> None:
        self._scroll_bg._resize_content()
        self._scroll_tr._resize_content()

    def _tr_visible(self) -> bool:
        try:
            return self.nb.select() == str(self.tab_tr)
        except Exception:                                # ventana ya cerrada
            return False

    def _tr_summary(self) -> str:
        label = self.v_tr.get()
        if label == KEEP_LABEL:
            cur = self.ctx.current.transition if self.ctx.current else None
            what = f"conservar la actual ({cur})" if cur else "conservar la actual (no tiene)"
        else:
            spec = self._specs.get(label)
            what = "ninguna" if spec is None or spec.kind == "none" else f"{spec.name}  ·  {spec.duration:g} s"
        return f"Transición: {what}      → se cambia en la pestaña «Transiciones»"

    def _sync_tr(self) -> None:
        """Pone al día los rótulos de las dos pestañas y reproduce SOLO si «Transiciones» está a la vista."""
        self.lbl_tr_sum.config(text=self._tr_summary())
        old_bg, new_bg = self._transition_pair()
        old = old_bg.name
        self.lbl_route.config(text=f"Vista previa: {old}   →   {new_bg.name}"
                              if new_bg else f"Vista previa: {old}   →   (ningún fondo coincide con el filtro)")
        if self._tr_visible():
            self._play()
        else:
            self._stop()

    def _transition_pair(self):
        """El fondo elegido como actual previsualiza la escena previa; otro fondo es el nuevo destino."""
        selected = self.model.current
        gray = scenes.Background("gris de referencia", "color", color="#808080")
        if selected is not None and self.ctx.current is not None \
                and selected.name == self.ctx.current_bg:
            old = self._bg_for(self.ctx.previous_bg) if self.ctx.previous else gray
            return old, self._bg_for(self.ctx.current_bg)
        old = self._bg_for(self.ctx.current_bg) if self.ctx.current else gray
        return old, selected

    def _on_tab(self) -> None:
        self._sync_tr()

    @staticmethod
    def _enable(btn, on: bool) -> None:
        btn.state(["!disabled"] if on else ["disabled"])

    def _step(self, d: int) -> None:
        if self.model.step(d) is not None:
            self._refresh()

    def _step_transition(self, d: int) -> None:
        if not self._transition_options:
            return
        try:
            index = self._transition_options.index(self.v_tr.get())
        except ValueError:
            index = 0
        self.v_tr.set(self._transition_options[(index + d) % len(self._transition_options)])
        self._show_transition_choice()
        self._play()

    def _render_transition_gallery(self) -> None:
        if not HAVE_TKIMG or self._old is None or self._new is None:
            return
        old_bg, new_bg = self._transition_pair()
        pair = (old_bg.name, old_bg.color, new_bg.name if new_bg else None,
                new_bg.color if new_bg else None)
        pair_changed = pair != self._transition_gallery_pair
        hovered_key = self._transition_gallery_hover_key
        if pair_changed:
            self._stop_transition_gallery_hover(render_static=False)
            self._transition_gallery_pair = pair
            self._transition_gallery_photos.clear()
            self._transition_gallery_hover_photos.clear()
        size = TRANSITION_CARD
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        self._transition_gallery_images = (
            self._old.resize(size, resampling),
            self._new.resize(size, resampling),
        )
        for key, card in self._transition_cards.items():
            if key != self._transition_gallery_hover_key:
                self._draw_transition_gallery_card(key, self._static_transition_gallery_photo(key))
        self._update_transition_card_selection()
        if pair_changed and hovered_key in self._transition_cards and self._pointer_inside_transition_card(hovered_key):
            self._on_transition_card_enter(hovered_key)

    def _transition_gallery_spec(self, key: str):
        spec = self._specs.get(key)
        if key == KEEP_LABEL:
            current = self.ctx.current.transition if self.ctx.current else None
            spec = next((item for item in self.ctx.transitions if item.name == current), None) if current else None
        return spec if spec is not None and spec.kind != "none" else None

    def _static_transition_gallery_photo(self, key: str):
        photo = self._transition_gallery_photos.get(key)
        if photo is not None:
            return photo
        images = self._transition_gallery_images
        if images is None:
            return None
        old_image, new_image = images
        spec = self._transition_gallery_spec(key)
        frame = tr.render(spec, old_image, new_image, spec.duration / 2) if spec else new_image
        photo = ImageTk.PhotoImage(frame.convert("RGB"))
        self._transition_gallery_photos[key] = photo
        return photo

    def _draw_transition_gallery_card(self, key: str, photo) -> None:
        if photo is None:
            return
        canvas = self._transition_cards[key]["canvas"]
        canvas.delete("all")
        canvas.create_image(TRANSITION_CARD[0] // 2, TRANSITION_CARD[1] // 2,
                            anchor="center", image=photo)

    def _on_transition_card_enter(self, key: str) -> None:
        if not HAVE_TKIMG or self._transition_gallery_images is None:
            return
        if self._transition_gallery_leave_job is not None:
            self.after_cancel(self._transition_gallery_leave_job)
            self._transition_gallery_leave_job = None
        if key == self._transition_gallery_hover_key:
            return
        self._stop_transition_gallery_hover(render_static=True)
        self._transition_gallery_hover_key = key
        self._transition_gallery_hover_started = time.monotonic()
        spec = self._transition_gallery_spec(key)
        if spec is None or spec.duration <= 0:
            return
        self._render_transition_gallery_hover(key, 0.0)
        self._transition_gallery_hover_job = self.after(
            33, lambda hovered=key: self._tick_transition_gallery_hover(hovered))

    def _on_transition_card_leave(self, key: str) -> None:
        if key != self._transition_gallery_hover_key or self._transition_gallery_leave_job is not None:
            return
        self._transition_gallery_leave_job = self.after(
            40, lambda hovered=key: self._finish_transition_gallery_leave(hovered))

    def _finish_transition_gallery_leave(self, key: str) -> None:
        self._transition_gallery_leave_job = None
        if key != self._transition_gallery_hover_key:
            return
        if not self._pointer_inside_transition_card(key):
            self._stop_transition_gallery_hover(render_static=True)

    def _pointer_inside_transition_card(self, key: str) -> bool:
        card = self._transition_cards[key]["frame"]
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        return (card.winfo_rootx() <= x < card.winfo_rootx() + card.winfo_width()
                and card.winfo_rooty() <= y < card.winfo_rooty() + card.winfo_height())

    def _stop_transition_gallery_hover(self, render_static: bool) -> None:
        if self._transition_gallery_hover_job is not None:
            self.after_cancel(self._transition_gallery_hover_job)
            self._transition_gallery_hover_job = None
        if self._transition_gallery_leave_job is not None:
            self.after_cancel(self._transition_gallery_leave_job)
            self._transition_gallery_leave_job = None
        key = self._transition_gallery_hover_key
        self._transition_gallery_hover_key = None
        if render_static and key in self._transition_cards:
            self._draw_transition_gallery_card(key, self._static_transition_gallery_photo(key))

    def _render_transition_gallery_hover(self, key: str, elapsed: float) -> None:
        images = self._transition_gallery_images
        if images is None:
            return
        spec = self._transition_gallery_spec(key)
        if spec is None or spec.duration <= 0:
            self._draw_transition_gallery_card(key, self._static_transition_gallery_photo(key))
            return
        old_image, new_image = images
        frame = tr.render(spec, old_image, new_image, elapsed % spec.duration)
        photo = ImageTk.PhotoImage(frame.convert("RGB"))
        self._transition_gallery_hover_photos[key] = photo
        self._draw_transition_gallery_card(key, photo)

    def _tick_transition_gallery_hover(self, key: str) -> None:
        if key != self._transition_gallery_hover_key:
            return
        elapsed = time.monotonic() - self._transition_gallery_hover_started
        self._render_transition_gallery_hover(key, elapsed)
        self._transition_gallery_hover_job = self.after(
            33, lambda hovered=key: self._tick_transition_gallery_hover(hovered))

    def _key(self, d: int) -> None:
        w = self.focus_get()
        if w is not None and w.winfo_class() in ("Entry", "TEntry", "TCombobox"):
            return                                   # con el cursor en un campo, las flechas son del campo
        if self._tr_visible():
            self._step_transition(d)
        else:
            self._step(d)

    def _on_filter(self) -> None:
        self.model.query = self.v_query.get()
        self.model.refilter()
        self._refresh()

    def _on_all(self) -> None:
        keep = self.model.current.name if self.model.current else None
        self.ctx = self._get_context(bool(self.v_all.get()))
        self._reload_items(keep)
        self._refresh()

    def _reload_items(self, keep=None) -> None:
        items = list(self.ctx.backgrounds)
        have = {b.name for b in items}
        self._new_colors = [c for c in self._new_colors if c.name not in have]       # los ya escritos dejan de ser «nuevos»
        items += self._new_colors
        self.model.items = items
        self.model.refilter(keep=keep)

    def _pick_color(self) -> None:
        _rgb, hexc = colorchooser.askcolor(parent=self, title="Color sólido para el fondo")
        if not hexc:
            return
        try:
            bg = scenes.color_background(hexc)
        except ValueError as exc:
            messagebox.showerror("Color", str(exc), parent=self)
            return
        if not any(c.name == bg.name for c in self._new_colors) and not any(b.name == bg.name for b in self.ctx.backgrounds):
            self._new_colors.append(bg)
        self.v_query.set("")
        self.model.add(bg)
        self._refresh()
        self._status(f"Color {bg.color} elegido: se escribirá  {scenes.definition_line(bg)}  al aplicarlo.", ok=True)

    # ------------------------------------------------------------------ transición
    def _effect(self):
        """KEEP · None (sin transición) · nombre de la transición."""
        label = self.v_tr.get()
        if label == KEEP_LABEL:
            return scene_edit.KEEP
        spec = self._specs.get(label)
        return None if spec is None or spec.kind == "none" else spec.name

    def _spec(self):
        """La transición que se ve en la vista previa (None = ninguna)."""
        label = self.v_tr.get()
        if label == KEEP_LABEL:
            cur = self.ctx.current.transition if self.ctx.current else None
            spec = next((s for s in self.ctx.transitions if s.name == cur), None) if cur else None
        else:
            spec = self._specs.get(label)
        return spec if spec is not None and spec.kind != "none" else None

    def _stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _play(self) -> None:
        self._stop()
        spec, new_bg = self._spec(), self.model.current
        label = self.v_tr.get()
        if label == KEEP_LABEL:
            cur = self.ctx.current.transition if self.ctx.current else None
            info = f"Conserva la que tiene: {cur}" if cur else "La escena actual no tiene transición."
        else:
            info = ""
        if spec is not None:
            info = (info + "  " if info else "") + f"{spec.name}: {spec.duration:g} s" + \
                   (f" · aproximada ({spec.note or 'dibujo aproximado'})" if spec.approx else "")
        self.lbl_tr.config(text=info)
        self.lbl_tr_sum.config(text=self._tr_summary())
        self._show_transition_choice()
        if not HAVE_TKIMG:
            self.cv_tr.delete("all")
            return
        if new_bg is None:
            self.cv_tr.delete("all")
            return
        preview_size = self._preview_size_for_canvas()
        self._preview_size = preview_size
        old_bg, new_bg = self._transition_pair()
        self._old = self._image(old_bg, preview_size).convert("RGBA")
        self._new = self._image(new_bg, preview_size).convert("RGBA")
        self._render_transition_gallery()
        self._spec_now = spec
        self._t0 = time.monotonic()
        self._tick()

    def _tick(self) -> None:
        spec = self._spec_now
        el = time.monotonic() - self._t0
        t = el - HOLD_START
        if spec is None or t >= (spec.duration if spec else 0):
            frame = self._new
        elif t <= 0:
            frame = self._old
        else:
            frame = tr.render(spec, self._old, self._new, t)
        self._show(self.cv_tr, frame.convert("RGB"), "tr")
        end = HOLD_START + (spec.duration if spec else 0.0) + HOLD_END
        if el >= end:
            if self.v_loop.get():
                self._t0 = time.monotonic()
            else:
                self._job = None
                return
        self._job = self.after(33, self._tick)

    # ------------------------------------------------------------------ acciones
    def _status(self, text: str, ok: bool) -> None:
        self.lbl_status.config(text=text, foreground="#1a6b1a" if ok else "#a01010")

    def _do(self, op: str) -> None:
        bg = self.model.current
        if op == "delete":
            cur = self.ctx.current
            back = f"Volverá a mandar «{self.ctx.previous.name}»." if self.ctx.previous else "No hay escena anterior."
            if not messagebox.askyesno("Eliminar escenario",
                                       f"Se borrará esta instrucción y su transición:\n\n{cur.text}\n\n{back}\n\n¿Continuar?",
                                       parent=self):
                return
        ok, msg = self._do_apply(op, bg, self._effect(), bool(self.v_reshow.get()))
        self._status(msg, ok)
        if ok:
            self._after_change(bg.name if bg else None)

    def _undo(self) -> None:
        ok, msg = self._do_undo()
        self._status(msg, ok)
        if ok:
            self._after_change(self.model.current.name if self.model.current else None)

    def _after_change(self, keep) -> None:
        self._cache.clear()                              # los fondos pueden haber cambiado (definición nueva...)
        self._warns.clear()
        self.ctx = self._get_context(bool(self.v_all.get()))
        self._reload_items(keep)
        self._fill_transitions()
        self._refresh()

    def destroy(self) -> None:
        self._stop()
        self._stop_transition_gallery_hover(render_static=False)
        for attr in ("_adapt_job", "_preview_resize_job"):
            job = getattr(self, attr, None)
            if job is not None:
                self.after_cancel(job)
                setattr(self, attr, None)
        super().destroy()
