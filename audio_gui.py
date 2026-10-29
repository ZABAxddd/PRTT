"""
audio_gui.py - Ventana «Música»: elige una pista, ajusta su fade-in / fade-out y su bucle sobre una gráfica con la línea
de tiempo de la canción, escúchala y escribe `play music` / `stop music` en el .rpy.

Toda la lógica está en audio.py (pistas, duración, onda, reproductor) y audio_edit.py (escritura en los .rpy); esta
ventana solo la dibuja. Se abre desde el botón «🎵 Música…» de PRTT.

La ventana recibe, en vez de datos, funciones del programa principal:
    get_context()                  -> audio_edit.Context (pistas, instrucción actual, pista vinculada al stop...)
    do_apply(op, track, opts)      -> (ok, mensaje)   op = replace_play | add_play | replace_stop | add_stop
    do_undo()                      -> (ok, mensaje)
    can_undo()                     -> bool

GRÁFICA (arrastra con el ratón)
    · pestaña Reproducir: el círculo naranja es el final del fade-in; un clic fija desde dónde escuchar.
    · pestaña Detener: la línea roja es el punto donde cae el `stop` (solo para la vista previa); el círculo naranja
      es el final del fade-out.
    · la zona verde es el bucle (`<loop N>` de la definición de la pista).
"""
import threading
import tkinter as tk
from tkinter import ttk
from i18n import _t

import audio
import audio_edit
from guiconf import fit_window

MAX_FADE = 30.0
PLAY_VIEWS = ((_t("Canción completa"), 0), (_t("Primeros 30 s"), 30), (_t("Primeros 10 s"), 10))
STOP_VIEWS = ((_t("Canción completa"), 0), ("Zoom ±15 s", 15), ("Zoom ±5 s", 5))
COL_BG, COL_PLOT = "#1b1b1b", "#242424"
COL_WAVE_DIM, COL_WAVE = "#2f4a66", "#5aa0e0"
COL_ENV, COL_LOOP, COL_STOP, COL_HEAD = "#ff9a1f", "#4caf50", "#e0463c", "#ffffff"


class Timeline(tk.Canvas):
    """Gráfica de una pista: onda, regla de tiempo, zona de bucle y la curva de volumen del fade."""
    LEFT, RIGHT, TOP, BOTTOM = 46, 16, 24, 30

    def __init__(self, master, mode: str, on_fade, on_point):
        super().__init__(master, height=250, bg=COL_BG, highlightthickness=1, highlightbackground="#555555")
        self.mode = mode                      # "play" | "stop"
        self.on_fade, self.on_point = on_fade, on_point
        self.track = None
        self.peaks = None
        self.fade = 1.0
        self.point = 0.0                      # play: desde dónde escuchar · stop: instante del stop
        self.loop = True
        self.view = 0                         # 0 = canción completa; si no, segundos de zoom
        self.playhead = None
        self._drag = None
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self.bind("<Motion>", self._hover)

    # ------------------------------------------------------------------ datos
    def set_data(self, track, peaks, fade: float, loop: bool) -> None:
        self.track, self.peaks, self.fade, self.loop = track, peaks, fade, loop
        self.point = max(0.0, min(self.point, self.total))
        self.redraw()

    @property
    def total(self) -> float:
        d = self.track.duration if self.track is not None else None
        return float(d) if d else 60.0

    def _window(self):
        total = self.total
        if not self.view:
            return 0.0, total
        if self.mode == "play":
            return 0.0, min(total, float(self.view))
        lo = max(0.0, self.point - self.view)
        hi = min(total, self.point + self.view + self.fade)
        return lo, max(hi, lo + 1.0)

    def _gain(self, t: float) -> float:
        return audio.fade_in_gain(t, self.fade) if self.mode == "play" else audio.fade_out_gain(t, self.point, self.fade)

    # ------------------------------------------------------------------ coordenadas
    def _box(self):
        return self.LEFT, self.TOP, max(self.winfo_width() - self.RIGHT, self.LEFT + 10), \
            max(self.winfo_height() - self.BOTTOM, self.TOP + 10)

    def _x(self, t: float) -> float:
        x0, _y0, x1, _y1 = self._box()
        a, b = self._window()
        return x0 + (t - a) / (b - a) * (x1 - x0)

    def _t(self, x: float) -> float:
        x0, _y0, x1, _y1 = self._box()
        a, b = self._window()
        return a + (x - x0) / (x1 - x0) * (b - a)

    def _handle(self):
        """(x, y) del círculo del fade."""
        _x0, y0, _x1, y1 = self._box()
        if self.mode == "play":
            return self._x(self.fade), y0
        return self._x(self.point + self.fade), y1

    # ------------------------------------------------------------------ dibujo
    def redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 120 or h < 100:
            return
        x0, y0, x1, y1 = self._box()
        self.create_rectangle(x0, y0, x1, y1, fill=COL_PLOT, outline="#444444")
        if self.track is None:
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, fill="#999999", text=_t("Elige una pista de la lista"))
            return
        a, b = self._window()
        self._draw_loop(x0, y0, x1, y1, a, b)
        self._draw_wave(x0, y0, x1, y1, a, b)
        self._draw_envelope(x0, y0, x1, y1, a, b)
        self._draw_ruler(x0, y0, x1, y1, a, b)
        self._draw_markers(x0, y0, x1, y1, a, b)
        if self.playhead is not None and a <= self.playhead <= b:
            px = self._x(self.playhead)
            self.create_line(px, y0, px, y1, fill=COL_HEAD, width=2)

    def _draw_loop(self, x0, y0, x1, y1, a, b) -> None:
        tr = self.track
        if tr.loop is None:
            return
        lo = max(tr.loop, a)
        if lo >= b:
            return
        xl = self._x(lo)
        self.create_rectangle(xl, y0, x1, y1, fill=COL_LOOP, stipple="gray12", outline="")
        if tr.loop >= a:
            self.create_line(xl, y0, xl, y1, fill=COL_LOOP, dash=(4, 3), width=2)
            self.create_text(xl + 4, y0 + 10, anchor="w", fill=COL_LOOP,
                             text=_t("↻ bucle desde {0}", audio.fmt_time(tr.loop)))

    def _draw_wave(self, x0, y0, x1, y1, a, b) -> None:
        yc, half_max = (y0 + y1) / 2, (y1 - y0) / 2 * 0.92
        total = self.total
        if not self.peaks:
            # sin onda: banda del volumen resultante (fade aplicado) para que la curva se vea igual
            top = []
            for px in range(int(x0), int(x1) + 1, 3):
                top.append((px, yc - half_max * 0.5 * self._gain(self._t(px))))
            bottom = [(px, 2 * yc - py) for px, py in reversed(top)]
            self.create_polygon([p for pt in top + bottom for p in pt], fill=COL_WAVE, stipple="gray50", outline="")
            self.create_text(x1 - 6, y1 - 8, anchor="e", fill="#777777",
                             text=_t("sin forma de onda (instala soundfile o pygame para verla)"))
            return
        n = len(self.peaks)
        for px in range(int(x0) + 1, int(x1), 2):
            t = self._t(px)
            if not 0 <= t <= total:
                continue
            p = self.peaks[min(int(t / total * n), n - 1)]
            half = half_max * p
            self.create_line(px, yc - half, px, yc + half, fill=COL_WAVE_DIM)
            g = half * self._gain(t)
            self.create_line(px, yc - g, px, yc + g, fill=COL_WAVE)

    def _draw_envelope(self, x0, y0, x1, y1, a, b) -> None:
        pts = []
        for px in range(int(x0), int(x1) + 1, 3):
            pts += [px, y1 - self._gain(self._t(px)) * (y1 - y0)]
        if len(pts) >= 4:
            self.create_line(*pts, fill=COL_ENV, width=2)
        hx, hy = self._handle()
        label = "fade-in" if self.mode == "play" else "fade-out"
        if x0 - 1 <= hx <= x1 + 1:
            self.create_oval(hx - 6, hy - 6, hx + 6, hy + 6, fill=COL_ENV, outline="#000000")
            self.create_text(min(hx + 10, x1 - 70), hy + (14 if self.mode == "play" else -14), anchor="w",
                             fill=COL_ENV, text=f"{label} {audio.fmt_num(self.fade)} s")
        else:
            self.create_text(x1 - 6, y0 + 10, anchor="e", fill=COL_ENV,
                             text=_t("{0} {1} s (fuera de la vista)", label, audio.fmt_num(self.fade)))

    def _draw_ruler(self, x0, y0, x1, y1, a, b) -> None:
        step = audio.nice_step(b - a)
        t = int(a / step) * step
        while t <= b + 1e-9:
            if t >= a:
                x = self._x(t)
                self.create_line(x, y1, x, y1 + 5, fill="#aaaaaa")
                self.create_line(x, y0, x, y1, fill="#303030")
                txt = audio.fmt_time(t) if step < 1 else f"{int(t // 60)}:{int(t % 60):02d}"
                self.create_text(x, y1 + 16, fill="#bbbbbb", text=txt)
            t += step
        for g, txt in ((1.0, "100%"), (0.5, "50%"), (0.0, "0%")):
            y = y1 - g * (y1 - y0)
            self.create_text(x0 - 6, y, anchor="e", fill="#999999", text=txt)
        if self.track.duration is None:
            self.create_text(x0 + 6, y1 - 8, anchor="w", fill="#a07030",
                             text=_t("duración desconocida: la regla es orientativa (60 s)"))

    def _draw_markers(self, x0, y0, x1, y1, a, b) -> None:
        if not a <= self.point <= b:
            return
        px = self._x(self.point)
        if self.mode == "stop":
            self.create_rectangle(px, y0, x1, y1, fill="#000000", stipple="gray25", outline="")
            self.create_line(px, y0, px, y1, fill=COL_STOP, width=2)
            self.create_text(px - 4, y0 + 10, anchor="e", fill=COL_STOP, text=f"stop · {audio.fmt_time(self.point)}")
        elif self.point > 0:
            self.create_line(px, y0, px, y1, fill="#bbbbbb", dash=(3, 3))
            self.create_text(px + 4, y1 - 10, anchor="w", fill="#bbbbbb", text=_t("▶ escuchar desde aquí"))
        if not self.loop:
            self.create_text(x1 - 6, y0 + 10, anchor="e", fill="#c0c0c0", text=_t("bucle desactivado (noloop)"))

    # ------------------------------------------------------------------ ratón
    def _hit(self, x: float, y: float):
        hx, hy = self._handle()
        if abs(x - hx) <= 10 and abs(y - hy) <= 12:
            return "fade"
        if self.mode == "stop" and abs(x - self._x(self.point)) <= 7:
            return "point"
        return None

    def _hover(self, event) -> None:
        self.config(cursor={"fade": "sb_h_double_arrow", "point": "sb_h_double_arrow"}.get(
            self._hit(event.x, event.y), "arrow"))

    def _press(self, event) -> None:
        if self.track is None:
            return
        x0, y0, x1, y1 = self._box()
        self._drag = self._hit(event.x, event.y)
        if self._drag is None and x0 <= event.x <= x1 and y0 <= event.y <= y1:
            self._drag = "point"                           # un clic en la gráfica fija el punto
        if self._drag:
            self._motion(event)

    def _motion(self, event) -> None:
        if not self._drag or self.track is None:
            return
        t = self._t(event.x)
        if self._drag == "fade":
            base = 0.0 if self.mode == "play" else self.point
            self.fade = round(max(0.0, min(MAX_FADE, t - base)) * 10) / 10
            self.on_fade(self.fade)
        else:
            self.point = round(max(0.0, min(self.total, t)) * 10) / 10
            self.on_point(self.point)
        self.redraw()


class AudioBrowser(tk.Toplevel):
    def __init__(self, master, get_context, do_apply, do_undo, can_undo):
        self._poll_job = None
        super().__init__(master)
        self.title(_t("Música · pistas, fades y bucles"))
        self.resizable(True, True)
        width, height = 1100, 680
        self.geometry(f"{width}x{height}")
        self._get_context, self._do_apply, self._do_undo, self._can_undo = get_context, do_apply, do_undo, can_undo
        self.ctx = get_context()
        self.player = audio.Player()
        self.sel = None                       # pista elegida en la lista
        self._view = []                       # pistas visibles (tras el filtro)
        self._peaks = {}                      # archivo -> picos (None si no se pudo leer)
        self._loading = set()
        self._done = []                       # resultados de los hilos que leen la onda
        self._silent = False                  # True mientras se rellena la lista por código
        self.v_query = tk.StringVar()
        self.v_in = tk.StringVar(value="1.0")
        self.v_out = tk.StringVar(value="1.0")
        self.v_loop = tk.BooleanVar(value=True)
        self.v_note = tk.BooleanVar(value=False)
        self.v_view_play = tk.StringVar(value=PLAY_VIEWS[1][0])
        self.v_view_stop = tk.StringVar(value=STOP_VIEWS[0][0])
        self._seed_from_script()
        self._build()
        self._fill_list(keep=self.sel.ref if self.sel else None)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        width, height = fit_window(self, width, height, parent=master, min_size=(min(860, width), min(520, height)))
        self.minsize(min(860, width), min(520, height))
        self._refresh_context_labels()
        self._sync()
        self._poll()
        self.grab_set()

    # ------------------------------------------------------------------ valores iniciales
    def _seed_from_script(self) -> None:
        """Arranca con los valores de la instrucción que ya hay en el script, si la hay."""
        c = self.ctx
        if c.play is not None:
            if c.play.fadein is not None:
                self.v_in.set(audio.fmt_num(c.play.fadein))
            self.v_loop.set(c.play.loop)
        if c.stop is not None and c.stop.fadeout is not None:
            self.v_out.set(audio.fmt_num(c.stop.fadeout))
        self.sel = (c.play.track if c.play is not None else None) or c.playing or \
            next((t for t in c.tracks if t.kind != "missing"), None)

    # ------------------------------------------------------------------ interfaz
    def _build(self) -> None:
        top = ttk.Frame(self, padding=(10, 8, 10, 2))
        top.pack(fill="x")
        self.lbl_where = ttk.Label(top, font=("TkDefaultFont", 10, "bold"))
        self.lbl_where.pack(anchor="w")
        self.lbl_play = ttk.Label(top, foreground="#333333", wraplength=1040, justify="left")
        self.lbl_play.pack(anchor="w")
        self.lbl_stop = ttk.Label(top, foreground="#777777", wraplength=1040, justify="left")
        self.lbl_stop.pack(anchor="w")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=6)

        left = ttk.Frame(panes)
        flt = ttk.Frame(left)
        flt.pack(fill="x")
        ttk.Label(flt, text=_t("Buscar:")).pack(side="left")
        ttk.Entry(flt, textvariable=self.v_query, width=22).pack(side="left", padx=6, fill="x", expand=True)
        self.v_query.trace_add("write", lambda *_: self._fill_list(keep=self.sel.ref if self.sel else None))
        self.tree = ttk.Treeview(left, columns=("name", "var", "dur"), show="headings", selectmode="browse", height=14)
        self.tree.heading("name", text=_t("Pista"))
        self.tree.heading("var", text="Variable")
        self.tree.heading("dur", text=_t("Dura"))
        self.tree.column("name", width=190)
        self.tree.column("var", width=70)
        self.tree.column("dur", width=52, anchor="e")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=(6, 0))
        self.tree.pack(fill="both", expand=True, pady=(6, 0))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        panes.add(left, weight=1)

        right = ttk.Frame(panes)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)
        self.tab_play, self.tab_stop = ttk.Frame(self.nb, padding=6), ttk.Frame(self.nb, padding=6)
        self.nb.add(self.tab_play, text=_t("  ▶ Reproducir música  "))
        self.nb.add(self.tab_stop, text=_t("  ■ Detener música  "))
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._on_tab())
        panes.add(right, weight=4)
        self._build_play(self.tab_play)
        self._build_stop(self.tab_stop)

        acts = ttk.Frame(self, padding=(10, 2, 10, 0))
        acts.pack(fill="x")
        self.b_undo = ttk.Button(acts, text=_t("↶ Deshacer último cambio"), command=self._undo)
        self.b_undo.pack(side="left")
        ttk.Button(acts, text=_t("Cerrar"), command=self.destroy).pack(side="right")
        self.lbl_status = ttk.Label(self, padding=(10, 4, 10, 8), wraplength=1040, justify="left")
        self.lbl_status.pack(fill="x")

    def _build_play(self, page) -> None:
        self.lbl_trk_play = ttk.Label(page, font=("TkDefaultFont", 10, "bold"), wraplength=720, justify="left")
        self.lbl_trk_play.pack(anchor="w")
        self.lbl_org_play = ttk.Label(page, foreground="#666666", wraplength=720, justify="left")
        self.lbl_org_play.pack(anchor="w", pady=(0, 4))
        self.tl_play = Timeline(page, "play", self._on_fade_in_drag, lambda _t: None)
        self.tl_play.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Fade-in (s):").pack(side="left")
        sp = ttk.Spinbox(row, from_=0, to=MAX_FADE, increment=0.1, width=6, textvariable=self.v_in,
                         command=self._sync)
        sp.pack(side="left", padx=(4, 12))
        self.v_in.trace_add("write", lambda *_: self._sync())
        ttk.Checkbutton(row, text=_t("Bucle (loop)"), variable=self.v_loop, command=self._sync).pack(side="left")
        ttk.Label(row, text=_t("   Vista:")).pack(side="left")
        cb = ttk.Combobox(row, textvariable=self.v_view_play, values=[v[0] for v in PLAY_VIEWS], state="readonly",
                          width=16)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync())
        ttk.Button(row, text=_t("■ Parar"), command=self._stop_preview).pack(side="right")
        ttk.Button(row, text=_t("▶ Escuchar"), command=self._listen).pack(side="right", padx=6)
        self.lbl_code_play = ttk.Label(page, font=("Consolas", 10), foreground="#1a4d8f", wraplength=720,
                                       justify="left")
        self.lbl_code_play.pack(anchor="w", pady=(8, 0))
        self.lbl_hint_play = ttk.Label(page, foreground="#777777", wraplength=720, justify="left", text=(
            _t("Arrastra el círculo naranja para el fade-in; un clic fija desde dónde escuchar. La música de Ren'Py ya se repite sola: «loop» marcado no escribe nada y desmarcado escribe `noloop`.")))
        self.lbl_hint_play.pack(anchor="w", pady=(2, 0))
        acts = ttk.Frame(page)
        acts.pack(fill="x", pady=(8, 0))
        self.b_rep_play = ttk.Button(acts, text=_t("Reemplazar música actual"), command=lambda: self._do("replace_play"))
        self.b_add_play = ttk.Button(acts, text=_t("Agregar nueva música"), command=lambda: self._do("add_play"))
        self.b_rep_play.pack(side="left", padx=(0, 6))
        self.b_add_play.pack(side="left")

    def _build_stop(self, page) -> None:
        self.lbl_link = ttk.Label(page, font=("TkDefaultFont", 10, "bold"), wraplength=720, justify="left")
        self.lbl_link.pack(anchor="w")
        self.lbl_org_stop = ttk.Label(page, foreground="#666666", wraplength=720, justify="left")
        self.lbl_org_stop.pack(anchor="w", pady=(0, 4))
        self.tl_stop = Timeline(page, "stop", self._on_fade_out_drag, lambda _t: None)
        self.tl_stop.pack(fill="both", expand=True)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Fade-out (s):").pack(side="left")
        ttk.Spinbox(row, from_=0, to=MAX_FADE, increment=0.1, width=6, textvariable=self.v_out,
                    command=self._sync).pack(side="left", padx=(4, 12))
        self.v_out.trace_add("write", lambda *_: self._sync())
        ttk.Label(row, text=_t("Vista:")).pack(side="left")
        cb = ttk.Combobox(row, textvariable=self.v_view_stop, values=[v[0] for v in STOP_VIEWS], state="readonly",
                          width=16)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync())
        ttk.Button(row, text=_t("■ Parar"), command=self._stop_preview).pack(side="right")
        ttk.Button(row, text=_t("▶ Escuchar el fade-out"), command=self._listen).pack(side="right", padx=6)
        self.lbl_code_stop = ttk.Label(page, font=("Consolas", 10), foreground="#1a4d8f", wraplength=720,
                                       justify="left")
        self.lbl_code_stop.pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(page, variable=self.v_note, command=self._sync, text=(
            _t("Anotar en el script qué pista detiene  (# detiene: Título)"))).pack(anchor="w", pady=(4, 0))
        ttk.Label(page, foreground="#777777", wraplength=720, justify="left", text=(
            _t("La pista que detiene el stop se averigua sola: es la última `play music` anterior a ese punto del guion. La línea roja solo sirve para la vista previa (en qué momento de la canción cae el stop)."))
                  ).pack(anchor="w", pady=(2, 0))
        acts = ttk.Frame(page)
        acts.pack(fill="x", pady=(8, 0))
        self.b_rep_stop = ttk.Button(acts, text=_t("Reemplazar stop actual"), command=lambda: self._do("replace_stop"))
        self.b_add_stop = ttk.Button(acts, text=_t("Agregar stop music"), command=lambda: self._do("add_stop"))
        self.b_rep_stop.pack(side="left", padx=(0, 6))
        self.b_add_stop.pack(side="left")

    # ------------------------------------------------------------------ valores de los controles
    @staticmethod
    def _num(var, default: float) -> float:
        try:
            return max(0.0, min(MAX_FADE, float(str(var.get()).replace(",", "."))))
        except ValueError:
            return default

    def _fadein(self) -> float:
        return self._num(self.v_in, 0.0)

    def _fadeout(self) -> float:
        return self._num(self.v_out, 0.0)

    def _audio_options(self) -> "audio_edit.Options":
        return audio_edit.Options(self._fadein(), self._fadeout(), bool(self.v_loop.get()), bool(self.v_note.get()))

    def _in_stop_tab(self) -> bool:
        return self.nb.index(self.nb.select()) == 1

    def _stop_track(self):
        """La pista que muestra la pestaña Detener: la que realmente se está deteniendo, si se averiguó."""
        link = self.ctx.link_for(self.ctx.stop is not None)
        return link.track if (link is not None and link.track is not None) else self.sel

    def _graph_track(self):
        return self._stop_track() if self._in_stop_tab() else self.sel

    # ------------------------------------------------------------------ lista de pistas
    def _fill_list(self, keep=None) -> None:
        q = self.v_query.get().strip().lower()
        self._view = [t for t in self.ctx.tracks
                      if not q or q in t.name.lower() or q in t.short.lower() or q in t.path.lower()]
        self._silent = True
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(self._view):
            audio.load_info(t)
            name = t.name + ("" if t.kind != "missing" else _t("  (sin definir)")) + \
                ("  ⚠" if t.kind != "missing" and not t.file else "")
            self.tree.insert("", "end", iid=str(i), values=(name, t.short, audio.fmt_time(t.duration)
                                                              if t.duration else "—"))
        pick = next((i for i, t in enumerate(self._view) if keep and t.ref == keep), 0 if self._view else None)
        if pick is not None:
            self.tree.selection_set(str(pick))
            self.tree.see(str(pick))
            self.sel = self._view[pick]
        self._silent = False
        self._sync()

    def _on_select(self, _event=None) -> None:
        if self._silent:
            return
        sel = self.tree.selection()
        if sel:
            self.sel = self._view[int(sel[0])]
            self._stop_preview()
            self._sync()

    def _on_tab(self) -> None:
        self._stop_preview()
        self._sync()

    # ------------------------------------------------------------------ onda en segundo plano
    def _load_peaks(self, track) -> None:
        f = track.file
        if not f or f in self._peaks or f in self._loading:
            return
        self._loading.add(f)

        def work():
            try:
                peaks = audio.waveform(f)
            except Exception:                    # cualquier fallo del decodificador: la gráfica va sin onda
                peaks = None
            self._done.append((f, peaks))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ refresco
    def _refresh_context_labels(self) -> None:
        c = self.ctx
        self.lbl_where.config(text=c.where)
        if c.play is not None:
            who = f"  →  «{c.play.track.name}»" if c.play.track else ""
            self.lbl_play.config(text=_t("Música actual:  {0}{1}", c.play.text, who))
        else:
            self.lbl_play.config(text=_t("Música actual:  ninguna (no hay `play music` antes de este diálogo en su label)"))
        txt = _t("Stop actual:  {0}", c.stop.text) if c.stop is not None else _t("Stop actual:  ninguno")
        if c.notes:
            txt += "\n" + "  ".join(c.notes)
        self.lbl_stop.config(text=txt)

    def _sync(self) -> None:
        """Pone al día la gráfica, los textos de código y los botones con lo elegido."""
        if not hasattr(self, "tl_stop"):
            return
        c = self.ctx
        t_play, t_stop = self.sel, self._stop_track()
        for t in (t_play, t_stop):
            if t is not None:
                audio.load_info(t)
                self._load_peaks(t)
        self.tl_play.view = dict(PLAY_VIEWS).get(self.v_view_play.get(), 0)
        self.tl_stop.view = dict(STOP_VIEWS).get(self.v_view_stop.get(), 0)
        loop = bool(self.v_loop.get())
        self.tl_play.set_data(t_play, self._peaks.get(t_play.file) if t_play and t_play.file else None,
                              self._fadein(), loop)
        self.tl_stop.set_data(t_stop, self._peaks.get(t_stop.file) if t_stop and t_stop.file else None,
                              self._fadeout(), True)
        self._describe_track(t_play, self.lbl_trk_play, self.lbl_org_play)
        # --- pestaña Detener: qué pista se detiene
        link = c.link_for(c.stop is not None)
        if link.state == "playing":
            self.lbl_link.config(text=_t("Detiene: {0}", link.title), foreground="#1a6b1a")
            self.lbl_org_stop.config(text=link.note)
        else:
            self.lbl_link.config(text=_t("Este stop no detiene ninguna pista conocida"), foreground="#a05000")
            self.lbl_org_stop.config(text=link.note[:1].upper() + link.note[1:] + "." if link.note else "")
        # --- código que se escribirá
        old = c.play.parsed if c.play is not None else None
        if t_play is not None and t_play.kind != "missing":
            add = audio_edit.build_play(t_play.ref, self._fadein(), loop)
            rep = audio_edit.build_play(t_play.ref, self._fadein(), loop, old) if c.play is not None else None
            self.lbl_code_play.config(text=(_t("Reemplazar →  {0}\n", rep) if rep else "") + _t("Agregar     →  {0}", add))
        else:
            self.lbl_code_play.config(text=_t("Elige una pista definida para generar el `play music`."))
        self.lbl_code_stop.config(text=f"→  {audio_edit.build_stop(self._fadeout())}")
        # --- botones
        ok_track = t_play is not None and t_play.kind != "missing"
        self.b_add_play.config(state="normal" if ok_track else "disabled")
        self.b_rep_play.config(state="normal" if ok_track and c.play is not None else "disabled")
        self.b_rep_stop.config(state="normal" if c.stop is not None else "disabled")
        self.b_undo.config(state="normal" if self._can_undo() else "disabled")

    @staticmethod
    def _describe_track(track, lbl_name, lbl_org) -> None:
        if track is None:
            lbl_name.config(text=_t("(sin pista)"))
            lbl_org.config(text="")
            return
        lbl_name.config(text=track.label)
        bits = [track.origin or track.path]
        if track.path:
            bits.append(track.path)
        if track.duration:
            bits.append(audio.fmt_time(track.duration))
        if track.kind != "missing" and not track.file:
            bits.append(_t("⚠ no encuentro el archivo (ni suelto en /game ni dentro de un .rpa): la gráfica va sin onda"))
        lbl_org.config(text="  ·  ".join(b for b in bits if b))

    # ------------------------------------------------------------------ gráfica -> controles
    def _on_fade_in_drag(self, value: float) -> None:
        self.v_in.set(audio.fmt_num(value))

    def _on_fade_out_drag(self, value: float) -> None:
        self.v_out.set(audio.fmt_num(value))

    # ------------------------------------------------------------------ vista previa
    def _listen(self) -> None:
        stop_mode = self._in_stop_tab()
        track = self._graph_track()
        if track is None or not track.file:
            self._status(_t("No encuentro el archivo de audio de esa pista; no hay nada que escuchar."), False)
            return
        if not self.player.ok:
            self._status(self.player.why, False)
            return
        audio.load_info(track)
        if stop_mode:
            point, fade = self.tl_stop.point, self._fadeout()
            lead = min(point, 3.0)                       # se oyen 3 s de música y luego cae el stop
            start = point - lead
            err = self.player.play(track.file, start, False, None, track.duration,
                                   lambda e: audio.fade_out_gain(e, lead, fade) if e < lead + fade else None)
            self._status(_t("Escuchando desde {0}: el stop cae a los {1} s.", audio.fmt_time(start), audio.fmt_num(lead)),
                         not err)
        else:
            fade, start = self._fadein(), self.tl_play.point
            err = self.player.play(track.file, start, bool(self.v_loop.get()), track.loop, track.duration,
                                   lambda e: audio.fade_in_gain(e, fade))
            self._status(_t("Escuchando «{0}» desde {1}{2}.", track.name, audio.fmt_time(start), _t(" con bucle") if self.v_loop.get() else ''), not err)
        if err:
            self._status(err, False)

    def _stop_preview(self) -> None:
        self.player.stop()
        self.tl_play.playhead = self.tl_stop.playhead = None
        self.tl_play.redraw()
        self.tl_stop.redraw()

    def _poll(self) -> None:
        """~25 veces por segundo: fades y bucle del reproductor, cursor de reproducción y ondas ya leídas."""
        self.player.tick()
        tl = self.tl_stop if self._in_stop_tab() else self.tl_play
        if self.player.active:
            tl.playhead = self.player.position()
            tl.redraw()
        elif tl.playhead is not None:
            tl.playhead = None
            tl.redraw()
        refresh = False
        while self._done:
            f, peaks = self._done.pop(0)
            self._peaks[f] = peaks
            self._loading.discard(f)
            refresh = True
        if refresh:
            self._sync()
        self._poll_job = self.after(40, self._poll)

    # ------------------------------------------------------------------ escribir en el .rpy
    def _status(self, text: str, ok: bool) -> None:
        self.lbl_status.config(text=text, foreground="#1a6b1a" if ok else "#a01010")

    def _do(self, op: str) -> None:
        self._stop_preview()
        track = self.sel if op in ("replace_play", "add_play") else None
        ok, msg = self._do_apply(op, track, self._audio_options())
        self._status(msg, ok)
        if ok:
            self._after_change()

    def _undo(self) -> None:
        ok, msg = self._do_undo()
        self._status(msg, ok)
        if ok:
            self._after_change()

    def _after_change(self) -> None:
        keep = self.sel.ref if self.sel else None
        self.ctx = self._get_context()
        self._refresh_context_labels()
        self._fill_list(keep=keep)

    def destroy(self) -> None:
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        player = getattr(self, "player", None)
        if player is not None:
            player.close()
        super().destroy()
