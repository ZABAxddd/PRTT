"""
scene_gui.py - Ventana «Escenarios»: carrusel de fondos, color sólido, transición con vista previa y los botones
Reemplazar / Agregar / Eliminar la instrucción `scene` del diálogo elegido.

Toda la lógica está en scenes.py (fondos y carrusel), transitions.py (efectos), scene_edit.py y scene_ctl.py
(escritura en los .rpy); esta ventana solo la dibuja. Se abre desde el botón «🎬 Escenarios…» de PRTT.

La ventana recibe, en vez de datos, funciones del programa principal:
    get_engine()                     -> preview.Preview (dibuja los fondos con tus mismas imágenes) o None
    get_context(include_all)         -> scene_ctl.Context (qué escena manda ahora, fondos, transiciones)
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

try:
    from PIL import Image, ImageTk
    HAVE_TKIMG = True
except ImportError:                                   # pragma: no cover
    HAVE_TKIMG = False

MAIN = (512, 288)          # fondo grande del carrusel
SIDE = (160, 90)           # los dos de los lados
PREV = (480, 270)          # vista previa de la transición (pestaña «Transiciones»)
HOLD_START, HOLD_END = 0.35, 0.7      # segundos que se ve el fondo viejo antes y el nuevo después
KEEP_LABEL = "(conservar la actual)"


class SceneBrowser(tk.Toplevel):
    def __init__(self, master, get_engine, get_context, do_apply, do_undo, can_undo):
        super().__init__(master)
        self.title("Escenarios · fondos y transiciones")
        self.transient(master)
        self.geometry("1040x700")
        self.minsize(900, 640)
        self._get_engine, self._get_context = get_engine, get_context
        self._do_apply, self._do_undo, self._can_undo = do_apply, do_undo, can_undo
        self._cache = {}              # (nombre, tamaño) -> imagen PIL RGB
        self._warns = {}              # nombre -> avisos del motor al dibujarlo
        self._photos = {}             # referencias a las PhotoImage (Tk las borra si no se guardan)
        self._new_colors = []         # colores creados aquí y aún sin escribir en los .rpy
        self._job = None
        self._t0 = 0.0
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
        self._refresh()
        self.bind("<Left>", lambda e: self._key(-1))
        self.bind("<Right>", lambda e: self._key(1))
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        x = master.winfo_rootx() + max((master.winfo_width() - self.winfo_width()) // 2, 0)
        y = master.winfo_rooty() + max((master.winfo_height() - self.winfo_height()) // 4, 0)
        self.geometry(f"+{x}+{y}")
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
        left = ttk.Frame(self.nb, padding=8)
        right = ttk.Frame(self.nb, padding=8)
        self.tab_bg, self.tab_tr = left, right
        self.nb.add(left, text="  Fondos  ")
        self.nb.add(right, text="  Transiciones  ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab())

        # --- carrusel: anterior · ACTUAL · siguiente
        row = ttk.Frame(left)
        row.pack(pady=(4, 0))
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
        self.lbl_name = ttk.Label(left, font=("TkDefaultFont", 11, "bold"))
        self.lbl_name.pack(pady=(8, 0))
        self.lbl_origin = ttk.Label(left, foreground="#666666")
        self.lbl_origin.pack()
        self.lbl_warn = ttk.Label(left, foreground="#a05000", wraplength=560, justify="center")
        self.lbl_warn.pack()

        flt = ttk.Frame(left)
        flt.pack(fill="x", pady=(10, 0))
        ttk.Label(flt, text="Filtrar:").pack(side="left")
        ent = ttk.Entry(flt, textvariable=self.v_query, width=22)
        ent.pack(side="left", padx=6)
        self.v_query.trace_add("write", lambda *_: self._on_filter())
        ttk.Checkbutton(flt, text="Mostrar todas las imágenes (no solo bg)", variable=self.v_all,
                        command=self._on_all).pack(side="left", padx=8)
        ttk.Button(flt, text="🎨 Color sólido…", command=self._pick_color).pack(side="right")
        self.lbl_tr_sum = ttk.Label(left, foreground="#555555", wraplength=640, justify="left")
        self.lbl_tr_sum.pack(anchor="w", pady=(10, 0))

        # --- pestaña «Transiciones»
        pick = ttk.Frame(right)
        pick.pack(fill="x")
        ttk.Label(pick, text="Transición (with):").pack(side="left")
        self.cb_tr = ttk.Combobox(pick, textvariable=self.v_tr, state="readonly", width=44)
        self.cb_tr.pack(side="left", padx=8)
        self.cb_tr.bind("<<ComboboxSelected>>", lambda e: self._play())
        self.lbl_tr = ttk.Label(right, foreground="#555555", wraplength=720, justify="left")
        self.lbl_tr.pack(anchor="w", pady=(4, 6))
        route = ttk.Frame(right)                         # el fondo de destino también se cambia desde aquí
        route.pack(fill="x")
        ttk.Button(route, text="◀", width=3, command=lambda: self._step(-1)).pack(side="left")
        self.lbl_route = ttk.Label(route, anchor="center", font=("TkDefaultFont", 10, "bold"))
        self.lbl_route.pack(side="left", fill="x", expand=True)
        ttk.Button(route, text="▶", width=3, command=lambda: self._step(1)).pack(side="right")
        self.cv_tr = tk.Canvas(right, width=PREV[0], height=PREV[1], highlightthickness=1, bg="#101010")
        self.cv_tr.pack(pady=(6, 0))
        ctl = ttk.Frame(right)
        ctl.pack(pady=(6, 0))
        ttk.Button(ctl, text="▶ Reproducir", command=self._play).pack(side="left")
        ttk.Checkbutton(ctl, text="Repetir", variable=self.v_loop, command=self._play).pack(side="left", padx=10)
        ttk.Label(right, foreground="#777777", wraplength=720, justify="center", text=(
            "Se ve el paso del fondo que manda ahora (izquierda) al fondo elegido en el carrusel. "
            "La transición que elijas aquí se usa al pulsar Reemplazar o Agregar."
        )).pack(pady=(6, 0))

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
        self.cb_tr.config(values=[KEEP_LABEL] + list(self._specs))
        self.v_tr.set(KEEP_LABEL if self.ctx.current is not None else tr.NONE_SPEC.label)

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
            self._show(self.cv_main, self._image(bg, MAIN), "main")
            for cv, d, tag in ((self.cv_prev, -1, "prev"), (self.cv_next, 1, "next")):
                nb = self.model.neighbor(d)
                if nb is None:
                    cv.delete("all")
                else:
                    self._show(cv, self._image(nb, SIDE), tag)
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
        bg = self.model.current
        old = self.ctx.current_bg or "(sin fondo anterior)"
        self.lbl_route.config(text=f"{old}   →   {bg.name}   ({self.model.index + 1} / {len(self.model.view)})"
                              if bg else f"{old}   →   (ningún fondo coincide con el filtro)")
        if self._tr_visible():
            self._play()
        else:
            self._stop()

    def _on_tab(self) -> None:
        self._sync_tr()

    @staticmethod
    def _enable(btn, on: bool) -> None:
        btn.state(["!disabled"] if on else ["disabled"])

    def _step(self, d: int) -> None:
        if self.model.step(d) is not None:
            self._refresh()

    def _key(self, d: int) -> None:
        w = self.focus_get()
        if w is not None and w.winfo_class() in ("Entry", "TEntry", "TCombobox"):
            return                                   # con el cursor en un campo, las flechas son del campo
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
        if not HAVE_TKIMG or new_bg is None:
            self.cv_tr.delete("all")
            return
        self._old = self._image(self._bg_for(self.ctx.current_bg), PREV).convert("RGBA")
        self._new = self._image(new_bg, PREV).convert("RGBA")
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
        super().destroy()
