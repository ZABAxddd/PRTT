"""
prtt_gui.py - Ventana del lector de diálogos (Tkinter, viene con Python en Windows).

Izquierda: archivos .rpy y sus labels (con nº de líneas de diálogo).
Centro:    las líneas de diálogo del archivo o label elegido (o resultados de la búsqueda).
Derecha:   la pose de cada personaje en escena para la línea seleccionada (sprites armados
           con las capas de las definiciones `image`), en celdas 1..N.
Abajo:     el texto completo de la línea seleccionada.
"""
import json
import os
import sys
import time
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import cells
import rpy_parser
import say_edit
import tfcatalog
import scene_ctl
import scene_gui
import rpy_writer
from dialogue import DialogueLine, build_index, count_lines, search
from history import EditHistory
from sprites import HAVE_PIL, SpriteBook, build_cell_views
from stage import build_timeline

try:
    from PIL import Image, ImageTk
    HAVE_TKIMG = True
except ImportError:
    HAVE_TKIMG = False

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))   # PRTT se ignora a sí mismo al releer /game
AUTOSAVE_DEFAULT = True        # auto-guardado activado al abrir
AUTOSAVE_DELAY_MS = 4000       # se guarda tras este tiempo SIN nuevos cambios
SETTINGS_FILE = os.path.join(TOOL_DIR, "prtt_settings.json")   # solo preferencias de PRTT, nunca el estado


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass                     # carpeta de solo lectura: la opción vale solo para esta sesión


class DialogueBrowser(tk.Tk):
    def __init__(self, db, game_dir: str):
        super().__init__()
        self.db = db
        self.index = build_index(db)
        self.book = SpriteBook(db, game_dir)
        self._timelines = {}     # label -> [Frame]
        self._photos = []        # referencias a las imágenes (Tk las borra si no se guardan)
        self.game_dir = game_dir
        self._pv = None          # motor de preview (se crea al usarlo la primera vez)
        self._pv_frames = []     # diálogos del label mostrado en la pestaña Preview
        self._pv_i = -1
        self._pv_img = None      # última imagen 1280x720 renderizada
        self._pv_photo = None
        self._pv_job = None      # after() de la animación en curso
        self._pv_t0 = 0.0
        self._pv_dirty = False
        self._pv_edits = {}      # (label, nº de diálogo) -> cells.CellLayout editado (clave estable al insertar líneas)
        self._catalog = None     # cells.SpriteCatalog (se crea al abrir el selector)
        self._pv_zw = []         # cuadritos de zorder dibujados sobre las celdas
        self._pv_sel = None      # celda seleccionada (1..N) o None
        self._pv_rects = []      # celdas dibujadas: (k, nombre, x0, y0, x1, y1) en px de pantalla
        self._pv_press_info = None     # (celda, x, y) donde se pulsó el ratón
        self._pv_drag_src = None       # celda que se está arrastrando
        self._pv_geom = (0, 0, 1.0)   # (x, y, escala) de la imagen dentro del canvas
        self._settings = _load_settings()
        # Comentarios «# PRTT» en las líneas que PRTT escribe: APAGADO salvo que el usuario lo active.
        self._write_comments = tk.BooleanVar(value=bool(self._settings.get("write_comments", False)))
        self.writer = rpy_writer.ScriptWriter(game_dir, comments=self._write_comments.get())
        self.hist = EditHistory()                         # deshacer / rehacer / cambios sin guardar
        self._scene_ctl = scene_ctl.SceneController(game_dir)   # escribe las instrucciones `scene` (selector de escenarios)
        self._scene_key = None                            # (label, nº) del diálogo con el que se abrió el selector
        self._mtimes = {}                  # archivo -> fecha de modificación cuando PRTT lo leyó
        self._autosave_job = None
        self._modal = 0                    # >0 mientras hay un diálogo modal abierto
        self._pv_cells_on = tk.BooleanVar(value=True)           # mostrar / ocultar las celdas
        self._autosave = tk.BooleanVar(value=AUTOSAVE_DEFAULT)
        self._title_base = f"PRTT - Lector de diálogos  |  {game_dir}"
        self.title(self._title_base)
        self.geometry("1500x760")
        self.minsize(900, 520)
        self._rows = {}          # iid de la derecha -> DialogueLine
        self._groups = {}        # iid de la izquierda -> lista de DialogueLine
        self._build_ui()
        self._fill_tree()
        self._stat_files()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-s>", lambda e: (self.save(), "break")[1])
        # Ctrl+Z / Ctrl+Y / Ctrl+Mayús+Z en cualquier pestaña (bind_all: también con el foco en
        # un árbol, un botón...). Un solo manejador mira la tecla y Mayús: así Bloq Mayús no
        # convierte Ctrl+Z en "rehacer".
        self.bind_all("<Control-KeyPress>", self._on_ctrl_key)
        self._update_ops()

    # ------------------------------------------------------------------ interfaz
    def _build_ui(self) -> None:
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        page = ttk.Frame(self.nb)
        self.page_pv = ttk.Frame(self.nb)
        self.nb.add(page, text="Diálogos")
        self.nb.add(self.page_pv, text="Preview")
        self._build_preview(self.page_pv)
        self.nb.bind("<<NotebookTabChanged>>", self._pv_on_tab)

        top = ttk.Frame(page, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Buscar:").pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query, width=40)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self._on_search())
        ttk.Button(top, text="Buscar", command=self._on_search).pack(side="left")
        ttk.Button(top, text="Limpiar", command=self._clear_search).pack(side="left", padx=4)
        self.info = ttk.Label(top, text="")
        self.info.pack(side="right")
        ttk.Button(top, text="✎ Editar diálogo (F2)", command=self._edit_dialogues).pack(side="left", padx=(12, 0))
        ttk.Button(top, text="🎬 Escenarios…", command=self._open_scenes).pack(side="left", padx=(6, 0))

        panes = ttk.PanedWindow(page, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8)

        left = ttk.Frame(panes)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")          # la barra se empaqueta ANTES: si no, queda cortada
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_group)
        panes.add(left, weight=1)

        right = ttk.Frame(panes)
        cols = ("n", "label", "who", "text", "where")
        self.rows = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        for col, title, width in (("n", "#", 45), ("label", "Label", 130), ("who", "Personaje", 110),
                                  ("text", "Texto", 520), ("where", "Archivo:línea", 150)):
            self.rows.heading(col, text=title)
            self.rows.column(col, width=width, minwidth=40, anchor="w", stretch=(col == "text"))
        sb2 = ttk.Scrollbar(right, orient="vertical", command=self.rows.yview)
        hsb = ttk.Scrollbar(right, orient="horizontal", command=self.rows.xview)
        self.rows.configure(yscrollcommand=sb2.set, xscrollcommand=hsb.set)
        # Las barras se empaquetan primero: las columnas suman más ancho que el panel y,
        # si la lista va antes, empuja la barra fuera de la ventana.
        sb2.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.rows.pack(side="left", fill="both", expand=True)
        self.rows.bind("<<TreeviewSelect>>", self._on_select_row)
        self.rows.bind("<Double-1>", lambda e: self._edit_dialogues()
                       if self.rows.identify_region(e.x, e.y) == "cell" else None)
        self.bind("<F2>", lambda e: self._edit_dialogues())
        panes.add(right, weight=4)

        side = ttk.Frame(panes, padding=(6, 0))
        self.side_title = ttk.Label(side, text="Pose en escena", font=("TkDefaultFont", 10, "bold"))
        self.side_title.pack(anchor="w", pady=(0, 4))
        self.cells_frame = ttk.Frame(side)
        self.cells_frame.pack(fill="x")
        self.side_info = tk.Text(side, height=9, wrap="word", state="disabled")
        self.side_info.pack(fill="both", expand=True, pady=(6, 0))
        panes.add(side, weight=3)

        self.detail = tk.Text(page, height=5, wrap="word", state="disabled")
        self.detail.pack(fill="x", padx=8, pady=(6, 8))

    # ------------------------------------------------------------------ pestaña Preview
    def _build_preview(self, page) -> None:
        bar = ttk.Frame(page, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="◀ Anterior", command=lambda: self._pv_step(-1)).pack(side="left")
        ttk.Button(bar, text="Siguiente ▶", command=lambda: self._pv_step(1)).pack(side="left", padx=4)
        self._pv_anim = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Animar", variable=self._pv_anim).pack(side="left", padx=8)
        ttk.Button(bar, text="Guardar PNG", command=self._pv_save).pack(side="left")
        ttk.Button(bar, text="Ajustes leídos", command=self._pv_settings).pack(side="left", padx=4)
        ttk.Button(bar, text="Copiar script", command=self._pv_copy).pack(side="left")
        ttk.Button(bar, text="🎬 Escenarios…", command=self._open_scenes).pack(side="left", padx=(8, 0))
        self._pv_status = ttk.Label(bar, text="Elige un diálogo en la pestaña «Diálogos».")
        self._pv_status.pack(side="right")
        ops = ttk.Frame(page, padding=(8, 0, 8, 4))
        ops.pack(fill="x")
        self._btn_save = ttk.Button(ops, text="Guardar en .rpy  (Ctrl+S)", command=self.save)
        self._btn_save.pack(side="left")
        self._btn_undo = ttk.Button(ops, text="↶ Deshacer (Ctrl+Z)", command=self._undo)
        self._btn_undo.pack(side="left", padx=(8, 2))
        self._btn_redo = ttk.Button(ops, text="↷ Rehacer (Ctrl+Y)", command=self._redo)
        self._btn_redo.pack(side="left")
        ttk.Checkbutton(ops, text="Auto-guardar", variable=self._autosave,
                        command=self._schedule_autosave).pack(side="left", padx=(12, 4))
        ttk.Checkbutton(ops, text="Comentarios # PRTT en el script", variable=self._write_comments,
                        command=self._on_comments_toggle).pack(side="left", padx=(0, 4))
        ttk.Checkbutton(ops, text="Mostrar celdas", variable=self._pv_cells_on,
                        command=self._pv_toggle_cells).pack(side="left", padx=8)
        ttk.Button(ops, text="Recargar .rpy", command=self._reload_from_disk).pack(side="left")
        self._save_status = ttk.Label(ops, text="")
        self._save_status.pack(side="right")
        # Cantidad de personajes: dropdown compacto junto a los botones (0 = ninguno). Al bajarla se
        # quitan solos los de la derecha, sin preguntar: Ctrl+Z lo devuelve.
        self._pv_count = tk.IntVar(value=0)
        ttk.Label(ops, text="Personajes:").pack(side="left", padx=(12, 2))
        self._pv_count_box = ttk.Combobox(ops, textvariable=self._pv_count, state="readonly", width=2,
                                          values=list(range(0, cells.MAX_CELLS + 1)))
        self._pv_count_box.pack(side="left", padx=(0, 8))
        self._pv_count_box.bind("<<ComboboxSelected>>", lambda e: self._pv_count_changed())
        self._pv_canvas = tk.Canvas(page, bg="black", highlightthickness=0)
        self._pv_canvas.pack(fill="both", expand=True, padx=8)
        # Panel del Textbox: qué dice la línea que se ve, selección rápida del hablante y «Editar línea».
        tb = ttk.LabelFrame(page, text="Textbox", padding=(8, 4))
        tb.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Button(tb, text="✎ Editar línea  (F2 · doble clic)", command=self._edit_current_line).pack(side="right")
        self._tb_speaker = tk.StringVar()
        self._tb_choices = []
        self._tb_box = ttk.Combobox(tb, textvariable=self._tb_speaker, state="readonly", width=30)
        self._tb_box.pack(side="right", padx=(6, 8))
        self._tb_box.bind("<<ComboboxSelected>>", lambda e: self._tb_quick_speaker())
        ttk.Label(tb, text="Hablante rápido:").pack(side="right")
        self._tb_info = ttk.Label(tb, text="", anchor="w")
        self._tb_info.pack(side="left", fill="x", expand=True)
        self._pv_warn = ttk.Label(page, text="", foreground="#a05000", wraplength=1100, justify="left")
        self._pv_warn.pack(fill="x", padx=8, pady=(4, 8))
        self._pv_canvas.bind("<Configure>", lambda e: self._pv_redraw())
        self._pv_canvas.bind("<Button-1>", self._pv_press)
        self._pv_canvas.bind("<B1-Motion>", self._pv_motion)
        self._pv_canvas.bind("<ButtonRelease-1>", self._pv_release)
        self._pv_canvas.bind("<Double-Button-1>", self._pv_double)    # doble clic en el textbox: editar la línea
        self._pv_canvas.bind("<Motion>", self._pv_hover)
        self.bind("<Right>", lambda e: self._pv_key(1))
        self.bind("<Return>", lambda e: self._pv_key(1))
        self.bind("<Left>", lambda e: self._pv_key(-1))

    def _pv_engine(self):
        if self._pv is None:
            if not HAVE_TKIMG:
                self._pv_status.config(text="Falta Pillow:  py -3 -m pip install pillow")
                return None
            try:
                import preview
                self._pv = preview.Preview(self.db, self.game_dir, self.book)
            except Exception as exc:      # no tumbar la ventana si el preview falla
                self._pv_status.config(text=f"No se pudo iniciar el preview: {exc}")
                return None
        return self._pv

    def _pv_visible(self) -> bool:
        return self.nb.select() == str(self.page_pv)

    def _pv_set(self, dl: DialogueLine) -> None:
        """Llamado al elegir una línea en la pestaña Diálogos."""
        frame = self._frame_for(dl)
        if frame is None:
            return
        self._pv_frames = self._timelines[dl.label]
        self._pv_i = frame.index
        self._pv_dirty = True

    def _pv_on_tab(self, _e=None) -> None:
        if self._pv_visible() and self._pv_dirty and self._pv_i >= 0:
            self._pv_dirty = False
            self._pv_show(animate=False)

    def _pv_key(self, d: int) -> None:
        if isinstance(self.focus_get(), (tk.Spinbox, tk.Entry, ttk.Entry)):
            return
        if self._pv_visible():
            self._pv_step(d)

    def _pv_step(self, d: int) -> None:
        j = self._pv_i + d
        if 0 <= j < len(self._pv_frames):
            self._pv_i = j
            self._pv_show(animate=self._pv_anim.get() and d > 0)

    def _pv_show(self, animate: bool) -> None:
        if self._pv_job is not None:
            self.after_cancel(self._pv_job)
            self._pv_job = None
        eng = self._pv_engine()
        if eng is None or not (0 <= self._pv_i < len(self._pv_frames)):
            return
        orig = self._pv_frames[self._pv_i]
        frame = self._pv_eff(orig)
        prev = self._pv_eff(self._pv_frames[self._pv_i - 1]) if self._pv_i > 0 else None
        self._pv_sync_count(orig)
        self._pv_status.config(
            text=f"{orig.event.label} · {self._pv_i + 1}/{len(self._pv_frames)} · {orig.event.where}")
        self._tb_refresh(orig)
        dur = eng.duration(frame, prev) if (animate and prev is not None) else 0.0
        if dur > 0:
            self._pv_t0 = time.monotonic()
            self._pv_tick(frame, prev, dur)
        else:
            self._pv_draw(eng.render(frame))

    def _pv_tick(self, frame, prev, dur: float) -> None:
        t = time.monotonic() - self._pv_t0
        if t >= dur:
            self._pv_job = None
            self._pv_draw(self._pv.render(frame))
            return
        self._pv_draw(self._pv.render(frame, prev, t, quick=True), quick=True)
        self._pv_job = self.after(15, lambda: self._pv_tick(frame, prev, dur))

    def _pv_draw(self, img, quick: bool = False) -> None:
        self._pv_img = img
        self._pv_warn.config(text="  ·  ".join(self._pv.warnings))
        self._pv_redraw(quick)

    def _pv_redraw(self, quick: bool = False) -> None:
        if self._pv_img is None:
            return
        cw = max(self._pv_canvas.winfo_width(), 2)
        ch = max(self._pv_canvas.winfo_height(), 2)
        s = min(cw / 1280, ch / 720)
        size = (max(int(1280 * s), 1), max(int(720 * s), 1))
        res = getattr(Image, "Resampling", Image)
        im = self._pv_img
        if size != im.size:
            im = im.resize(size, res.BILINEAR if quick else res.LANCZOS)
        self._pv_photo = ImageTk.PhotoImage(im)
        self._pv_canvas.delete("all")
        self._pv_canvas.create_image(cw // 2, ch // 2, image=self._pv_photo)
        self._pv_geom = (cw // 2 - size[0] // 2, ch // 2 - size[1] // 2, s)
        if not quick:                      # durante la animación no se dibujan las celdas
            self._pv_draw_cells()

    # ------------------------------------------------------------------ celdas de personajes
    def _pv_frame(self):
        return self._pv_frames[self._pv_i] if 0 <= self._pv_i < len(self._pv_frames) else None

    def _pv_layout(self, create: bool = False):
        """Layout del diálogo actual: el editado, o el que trae el guion (detección previa).
        Solo se guarda como edición (create=True) cuando el usuario cambia algo; así los diálogos
        sin tocar conservan sus animaciones originales."""
        frame = self._pv_frame()
        if frame is None:
            return None
        key = self._ekey(frame)
        lay = self._pv_edits.get(key)
        if lay is None:
            lay = cells.layout_from_stage(frame.stage)
            if create:
                self._pv_edits[key] = lay
        return lay

    def _pv_eff(self, frame):
        lay = self._pv_edits.get(self._ekey(frame))
        return cells.apply_layout(frame, lay) if lay is not None else frame

    def _pv_rerender(self, msg: str = "") -> None:
        if self._pv_job is not None:
            self.after_cancel(self._pv_job)
            self._pv_job = None
        frame = self._pv_frame()
        if self._pv is None or frame is None:
            return
        self._pv_draw(self._pv.render(self._pv_eff(frame)))
        if msg:
            self._pv_status.config(text=msg)

    def _pv_sync_count(self, frame) -> None:
        """Al cambiar de diálogo: el selector toma lo editado antes, o los que ya trae el guion."""
        lay = self._pv_edits.get(self._ekey(frame))
        if lay is None:
            lay = cells.layout_from_stage(frame.stage)
        self._pv_count.set(max(0, min(lay.count, cells.MAX_CELLS)))
        self._pv_sel = None

    def _pv_count_changed(self) -> None:
        lay = self._pv_layout()
        if lay is None:
            return
        n = self._pv_count.get()
        if n == lay.count:
            return
        before = self._hist_begin()                  # sin alertas: Ctrl+Z deshace la remoción
        lay = self._pv_layout(create=True)
        eff, moves = lay.set_count(n)
        msg = "  ·  ".join(moves) if moves else f"{eff} personaje(s) en escena"
        self._hist_end(before, msg)
        self._pv_count.set(eff)
        self._pv_sel = None
        self._pv_rerender(msg)

    def _pv_draw_cells(self) -> None:
        c = self._pv_canvas
        c.delete("cell")
        for w in self._pv_zw:
            w.destroy()
        self._pv_zw = []
        self._pv_rects = []
        if not self._pv_cells_on.get():          # celdas ocultas: ni bordes, ni textos, ni cuadritos Z
            return
        lay = self._pv_layout()
        eng = self._pv
        if lay is None or lay.count <= 0 or eng is None or self._pv_img is None:
            return
        n = lay.count
        ox, oy, s = self._pv_geom
        small = ("TkDefaultFont", max(8, int(13 * s)), "bold")
        for k, name, x0, y0, x1, y1 in eng.cell_rects(n):
            self._pv_rects.append((k, name, x0, y0, x1, y1))
            chosen = (self._pv_sel == k)
            r = (ox + x0 * s, oy + y0 * s, ox + x1 * s, oy + y1 * s)
            color, w = ("#ffd400", 3) if chosen else ("#ffffff", 2)
            # Solo un borde sólido (sin relleno, sin trama, sin guiones). El halo oscuro por debajo
            # hace que el borde se vea igual sobre fondos claros y oscuros.
            c.create_rectangle(*r, outline="#000000", width=w + 2, tags="cell")
            c.create_rectangle(*r, outline=color, width=w, tags="cell")
            p = lay.slots.get(k)
            self._pv_label(r[0] + 6, r[1] + 4, lay.at_of(k) if p is not None else name, small, anchor="nw")
            label = p.image if p is not None else "+ elegir personaje"
            self._pv_label((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, label, small,
                           width=max(40, r[2] - r[0] - 12))
            if p is not None:
                self._pv_add_z(k, p.zorder, r)

    def _pv_label(self, x, y, text: str, font, **kw) -> None:
        """Texto blanco con sombra negra de 1 px (legible sobre cualquier sprite)."""
        c = self._pv_canvas
        c.create_text(x + 1, y + 1, text=text, fill="#000000", font=font, tags="cell", **kw)
        c.create_text(x, y, text=text, fill="#ffffff", font=font, tags="cell", **kw)

    def _pv_toggle_cells(self) -> None:
        self._pv_sel = None
        self._pv_draw_cells()
        self._pv_status.config(text="Celdas visibles: clic en una celda para editarla."
                               if self._pv_cells_on.get() else "Celdas ocultas.")

    def _pv_add_z(self, slot: int, z: int, r) -> None:
        """Cuadrito numérico de zorder en la esquina superior derecha de la celda."""
        fr = tk.Frame(self._pv_canvas, bg="#202020", padx=2, pady=1)
        tk.Label(fr, text="Z", fg="#ffffff", bg="#202020", font=("TkDefaultFont", 8, "bold")).pack(side="left")
        var = tk.StringVar(value=str(z))
        sp = tk.Spinbox(fr, from_=-99, to=99, width=3, textvariable=var, justify="center",
                        command=lambda: self._pv_set_z(slot, var))
        sp.pack(side="left")
        sp.bind("<Return>", lambda e: (self._pv_set_z(slot, var), "break")[1])
        sp.bind("<FocusOut>", lambda e: self._pv_set_z(slot, var))
        self._pv_canvas.create_window(r[2] - 4, r[1] + 4, anchor="ne", window=fr, tags="cell")
        self._pv_zw.append(fr)

    def _pv_set_z(self, slot: int, var) -> None:
        try:
            z = max(-99, min(99, int(var.get())))
        except ValueError:
            var.set("0")
            z = 0
        cur = self._pv_layout()
        if cur is None or slot not in cur.slots or cur.slots[slot].zorder == z:
            return
        before = self._hist_begin()
        lay = self._pv_layout(create=True)
        lay.set_zorder(slot, z)
        self._hist_end(before, f"{lay.slots[slot].image}: zorder {z}")
        # se redibuja después: este evento viene del propio cuadrito, que se destruye al redibujar
        self.after_idle(lambda: self._pv_rerender(f"{lay.slots[slot].image}: zorder {z}"))

    def _pv_cell_at(self, event) -> Optional[int]:
        """Nº de la celda bajo el puntero, o None."""
        ox, oy, sc = self._pv_geom
        if sc <= 0:
            return None
        ix, iy = (event.x - ox) / sc, (event.y - oy) / sc
        for k, _name, x0, y0, x1, y1 in self._pv_rects:
            if x0 <= ix <= x1 and y0 <= iy <= y1:
                return k
        return None

    def _pv_press(self, event) -> None:
        self._pv_canvas.focus_set()          # saca el foco de los cuadritos Z (para que ← → funcionen)
        self._pv_drag_src = None
        if self._pv_in_textbox(event):
            self._pv_press_info = None
            return
        k = self._pv_cell_at(event)
        self._pv_press_info = (k, event.x, event.y) if k is not None else None

    def _pv_motion(self, event) -> None:
        info = self._pv_press_info
        if info is None:
            return
        k, x0, y0 = info
        if self._pv_drag_src is None:
            lay = self._pv_layout()
            if abs(event.x - x0) + abs(event.y - y0) < 8 or lay is None or k not in lay.slots:
                return                                   # aún es un clic (o la celda está vacía)
            self._pv_drag_src = k
            self._pv_canvas.config(cursor="fleur")
        self._pv_draw_drag(event)

    def _pv_draw_drag(self, event) -> None:
        """Resalta la celda de destino y muestra el personaje que se lleva bajo el puntero."""
        c = self._pv_canvas
        c.delete("drag")
        lay = self._pv_layout()
        if lay is None or self._pv_drag_src not in lay.slots:
            return
        dst = self._pv_cell_at(event)
        ox, oy, sc = self._pv_geom
        if dst is not None and dst != self._pv_drag_src:
            for k, _n, x0, y0, x1, y1 in self._pv_rects:
                if k == dst:
                    c.create_rectangle(ox + x0 * sc, oy + y0 * sc, ox + x1 * sc, oy + y1 * sc,
                                       outline="#00e0ff", width=4, tags="drag")
        label = lay.slots[self._pv_drag_src].image
        c.create_text(event.x + 1, event.y - 13, text=label, fill="#000000", tags="drag",
                      font=("TkDefaultFont", 11, "bold"))
        c.create_text(event.x, event.y - 14, text=label, fill="#00e0ff", tags="drag",
                      font=("TkDefaultFont", 11, "bold"))

    def _pv_release(self, event) -> None:
        info, src = self._pv_press_info, self._pv_drag_src
        self._pv_press_info = self._pv_drag_src = None
        self._pv_canvas.delete("drag")
        self._pv_canvas.config(cursor="")
        if info is None:
            return
        if src is None:                                  # no hubo arrastre: era un clic normal
            self._pv_cell_clicked(info[0], event)
            return
        dst = self._pv_cell_at(event)
        if dst is None or dst == src:
            self._pv_status.config(text="Arrastre cancelado")
            return
        self._pv_edit(lambda L: L.drag(src, dst))        # el ocupante de `dst` va a la vacía más próxima

    def _pv_cell_clicked(self, k: int, event) -> None:
        name = next((n for kk, n, *_ in self._pv_rects if kk == k), str(k))
        self._pv_sel = k
        self._pv_draw_cells()
        self._pv_status.config(text=f"Celda {name} seleccionada  ·  arrástrala para moverla")
        lay = self._pv_layout()
        if lay is not None and k not in lay.slots:
            self._pv_pick(k)
        else:
            self._pv_menu(k, event)

    def _pv_menu(self, k: int, event) -> None:
        lay = self._pv_layout()
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Cambiar personaje / pose…", command=lambda: self._pv_pick(k))
        if k in lay.slots:
            m.add_command(label="Previsualizar su animación…", command=lambda: self._pv_open_tf_preview(k))
        for j in range(1, lay.count + 1):
            if j == k:
                continue
            other = lay.slots.get(j)
            txt = (f"Intercambiar con {lay.name(j)} ({other.image})" if other is not None
                   else f"Mover a {lay.name(j)} (vacía)")
            m.add_command(label=txt, command=lambda j=j: self._pv_edit(lambda L: L.swap(k, j)))
        m.add_separator()
        m.add_command(label="Quitar de la escena", command=lambda: self._pv_edit(lambda L: L.remove(k)))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _pv_edit(self, fn) -> None:
        before = self._hist_begin()
        lay = self._pv_layout(create=True)
        if lay is None:
            return
        try:
            msg = fn(lay)
        except ValueError as exc:
            self._hist_abort(before)                  # la edición falló: no queda nada a medias
            msg = str(exc)
        else:
            self._hist_end(before, msg or "edición de celdas")
        self._pv_sel = None
        self._pv_rerender(msg)

    def _pv_catalog(self):
        if self._catalog is None:
            self._catalog = cells.SpriteCatalog(self.db, self.book)
        return self._catalog

    def _pv_pick(self, k: int) -> None:
        lay = self._pv_layout()
        if lay is None:
            return
        cat = self._pv_catalog()
        if not cat.tags():
            messagebox.showinfo("Sin personajes", "No encontré ninguna definición «image personaje pose» en tus .rpy.")
            return
        known = set(getattr(self.db, "transforms", ())) | set(getattr(self.db, "transform_defs", {}))
        cur = self._pv_frame()
        dlg = _PosePicker(self, cat, [], current=lay.slots.get(k),
                          default_transform=lay.at_of(k) if k in lay.slots else lay.name(k),
                          known=known, title=f"Celda {lay.name(k)}", db=self.db,
                          engine=self._pv_engine(), count=lay.count, slot=k,
                          bg=cur.stage.bg if cur is not None else "")
        self._modal += 1
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        if dlg.result is not None:
            self._pv_edit(lambda L: L.place_at(dlg.transform, dlg.result, slot=k, reflow=dlg.reflow))

    def _pv_open_tf_preview(self, k: int) -> None:
        """Ventana de animación del personaje de la celda k con SU transform actual."""
        lay = self._pv_layout()
        eng = self._pv_engine()
        if lay is None or eng is None or k not in lay.slots:
            return
        p = lay.slots[k]
        cur = self._pv_frame()
        _TransformPreview(self, eng, lambda: p.tag, lambda: p.attrs, lambda: lay.at_of(k),
                          lay.count, k, cur.stage.bg if cur is not None else "")

    def _pv_copy(self) -> None:
        lay = self._pv_layout()
        text = cells.to_rpy(lay) if lay is not None else ""
        if not text:
            self._pv_status.config(text="No hay personajes en las celdas.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._pv_status.config(text="Copiado:  " + text.replace("\n", "  ·  "))

    def _pv_save(self) -> None:
        if self._pv_img is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")],
                                            initialfile="preview.png")
        if path:
            self._pv_img.save(path)

    def _pv_settings(self) -> None:
        eng = self._pv_engine()
        if eng is None:
            return
        win = tk.Toplevel(self)
        win.title("Ajustes de la caja de texto leídos de tus .rpy")
        txt = tk.Text(win, width=120, height=28, font=("Courier New", 9))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", "\n".join(eng.gui.describe()))
        txt.config(state="disabled")

    # ------------------------------------------------------------------ escenarios (fondos y transiciones)
    def _scene_frame(self):
        """El diálogo con el que se abrió el selector, releído de los scripts actuales."""
        if self._scene_key is None:
            return None
        label, idx = self._scene_key
        frames = self._timeline(label) if label in self.db.labels else []
        return frames[idx] if 0 <= idx < len(frames) else None

    def _scene_find_image(self, path: str):
        """Máscara de un ImageDissolve de tus .rpy (para dibujar su transición); None si no se encuentra."""
        real = self.book.resolve_file(path)
        if not real or not HAVE_TKIMG:
            return None
        try:
            with Image.open(real) as im:
                return im.convert("L")
        except (OSError, ValueError):
            return None

    def _scene_context(self, include_all: bool = False):
        frame = self._scene_frame()
        if frame is None:
            raise RuntimeError("No encuentro el diálogo: pulsa «Recargar .rpy».")
        return scene_ctl.build_context(self.db, frame, self._scene_find_image, include_all)

    def _scene_apply(self, op: str, bg, effect, reshow: bool):
        frame = self._scene_frame()
        if frame is None:
            return False, "No encuentro el diálogo: pulsa «Recargar .rpy»."
        ok, msg, full = self._scene_ctl.apply(self.db, frame, op, bg, effect, reshow, self._mtimes)
        if ok:
            self._scene_reloaded(full)
        return ok, msg

    def _scene_undo(self):
        ok, msg = self._scene_ctl.undo()
        if ok:
            self._scene_reloaded(True)            # puede haberse quitado una definición: se relee todo
        return ok, msg

    def _scene_reloaded(self, full: bool) -> None:
        """Tras escribir un `scene`: releer los .rpy (tus ediciones de poses pendientes se conservan) y redibujar."""
        self._reload_db(full=full)
        if self._pv_visible():
            self._pv_show(animate=False)
        else:
            self._pv_dirty = True

    def _open_scenes(self) -> None:
        frame = self._pv_frame()
        if frame is None:
            messagebox.showinfo("Escenarios", "Elige primero un diálogo en la pestaña «Diálogos».", parent=self)
            return
        self._scene_key = self._ekey(frame)
        dlg = scene_gui.SceneBrowser(self, self._pv_engine, self._scene_context, self._scene_apply,
                                     self._scene_undo, self._scene_ctl.can_undo)
        self._modal += 1                           # sin auto-guardado mientras la ventana está abierta
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        self._scene_key = None

    # ------------------------------------------------------------------ edición, guardado y deshacer
    @staticmethod
    def _ekey(frame):
        """Clave de un diálogo para las ediciones. No usa nº de línea: sigue valiendo aunque al
        guardar se inserten líneas en el .rpy."""
        return (frame.event.label, frame.index)

    def _timeline(self, label: str):
        frames = self._timelines.get(label)
        if frames is None:
            frames = self._timelines[label] = build_timeline(self.db, label)
        return frames

    def _hist_begin(self):
        return EditHistory.snapshot(self._pv_edits)

    def _hist_end(self, before, desc: str) -> None:
        if self.hist.record(before, self._pv_edits, desc):
            self._on_changed()

    def _hist_abort(self, before) -> None:
        self._pv_edits = before

    def _is_dirty(self) -> bool:
        return self.hist.dirty(self._pv_edits)

    def _on_changed(self) -> None:
        self._update_ops()
        self._schedule_autosave()

    def _update_ops(self, note: str = "") -> None:
        dirty = self._is_dirty()
        self.title(("* " if dirty else "") + self._title_base)
        self._btn_save.state(["!disabled"] if dirty else ["disabled"])
        self._btn_undo.state(["!disabled"] if self.hist.can_undo() else ["disabled"])
        self._btn_redo.state(["!disabled"] if self.hist.can_redo() else ["disabled"])
        n = self.hist.pending(self._pv_edits)
        self._save_status.config(text=note or (
            f"● {n} diálogo(s) sin guardar" if dirty else "Sin cambios pendientes"))

    # --- deshacer / rehacer
    def _editing_text(self) -> bool:
        """¿El foco está en un campo de texto editable? Ahí Ctrl+Z/Y son del campo, no del historial."""
        try:
            w = self.focus_get()
            if w is None:
                return False
            if w.winfo_class() in ("Entry", "TEntry", "Spinbox", "TSpinbox", "TCombobox", "Text"):
                return str(w.cget("state")) not in ("disabled", "readonly")
        except (KeyError, tk.TclError):      # Tk a veces falla al buscar el foco (popdown de un Combobox)
            pass
        return False

    def _on_ctrl_key(self, e):
        key = (e.keysym or "").lower()
        if sys.platform.startswith("win") and e.keycode in (89, 90):
            key = "y" if e.keycode == 89 else "z"      # teclados no latinos: la tecla física Z / Y
        if key not in ("z", "y"):
            return None
        if self._modal or self.grab_current() not in (None, self):
            return None                                # hay un diálogo modal abierto
        if self._editing_text():
            return None
        if key == "y" or (e.state & 0x1):              # Ctrl+Y  ·  Ctrl+Mayús+Z
            self._redo()
        else:
            self._undo()
        return "break"

    def _undo(self) -> None:
        self._history_step(self.hist.undo, "Deshecho", "No hay nada que deshacer.")

    def _redo(self) -> None:
        self._history_step(self.hist.redo, "Rehecho", "No hay nada que rehacer.")

    def _history_step(self, op, verb: str, empty: str) -> None:
        res = op(self._pv_edits)
        if res is None:
            self._pv_status.config(text=empty)
            return
        desc, restored = res
        changed = sorted(k for k in set(self._pv_edits) | set(restored)
                         if self._pv_edits.get(k) != restored.get(k))
        self._pv_edits = restored
        frame = self._pv_frame()
        if changed and (frame is None or self._ekey(frame) not in changed):
            label, idx = changed[0]                     # lleva a ver el diálogo que se ha deshecho
            frames = self._timeline(label) if label in self.db.labels else []
            if 0 <= idx < len(frames):
                self._pv_frames, self._pv_i = frames, idx
        if not self._pv_visible() and 0 <= self._pv_i < len(self._pv_frames):
            self.nb.select(self.page_pv)               # así se ve lo que se ha deshecho
        self._pv_show(animate=False)
        self._pv_status.config(text=f"{verb}: {desc}")
        self._on_changed()

    # --- guardar en los .rpy
    def _stat_files(self) -> None:
        self._mtimes = {}
        for rel in self.db.files:
            try:
                self._mtimes[rel] = os.stat(os.path.join(self.game_dir, *rel.split("/"))).st_mtime_ns
            except OSError:
                pass

    def _on_comments_toggle(self) -> None:
        on = bool(self._write_comments.get())
        self.writer.comments = on
        self._settings["write_comments"] = on
        _save_settings(self._settings)
        self._pv_status.config(text=("Se añadirá «# PRTT» al final de las líneas que PRTT escriba."
                                     if on else "PRTT no escribirá comentarios en el script."))

    def _collect_changes(self):
        changes, errors = [], []
        saved = self.hist.saved
        for key in sorted(set(self._pv_edits) | set(saved)):
            lay = self._pv_edits.get(key)
            if lay == saved.get(key):
                continue
            label, idx = key
            frames = self._timeline(label) if label in self.db.labels else []
            if not 0 <= idx < len(frames):
                errors.append(f"{label} #{idx + 1}: no encuentro ese diálogo en los scripts.")
                continue
            # make_change recoge la ventana de instrucciones del diálogo (para reemplazar sus show/hide
            # en su sitio) y quiénes ya estaban en escena antes (para decidir hide o borrar)
            changes.append(rpy_writer.make_change(key, frames[idx], lay, frames[idx - 1] if idx else None, self.db))
        return changes, errors

    def save(self, auto: bool = False) -> bool:
        """Escribe las ediciones pendientes en los .rpy. True si todo quedó guardado."""
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        self.writer.comments = bool(self._write_comments.get())
        changes, errors = self._collect_changes()
        wrote = False
        if changes:
            for r in self.writer.apply(changes, self._mtimes):
                if r.ok:
                    self.hist.mark_saved(r.key, self._pv_edits.get(r.key))
                    wrote = True
                else:
                    errors.append(r.msg)
        if wrote:
            self._reload_db(full=False)          # los números de línea cambiaron: se vuelve a leer
        if not changes and not errors:
            self._update_ops()
            return True
        if errors:
            self._update_ops(note="No se pudo guardar todo")
            if auto:
                self._pv_status.config(text="Auto-guardado: " + errors[0])
            else:
                messagebox.showerror("Guardar en los .rpy", "\n\n".join(errors[:6]), parent=self)
            return False
        self._update_ops(note=("Auto-guardado " if auto else "Guardado ") + time.strftime("%H:%M:%S"))
        return True

    # --- releer los scripts
    def _reload_db(self, full: bool) -> None:
        """Vuelve a leer /game. full=False (tras guardar): solo se actualizan los números de línea.
        full=True (botón «Recargar .rpy»): se reconstruye todo, por si editaste los scripts fuera."""
        try:
            db = rpy_parser.load(self.game_dir, exclude_dirs=(TOOL_DIR,))
        except Exception as exc:                  # no tumbar la ventana
            self._pv_status.config(text=f"No se pudo releer la carpeta: {exc}")
            return
        cur = self._pv_frame()
        cur_key = self._ekey(cur) if cur is not None else None
        had_engine = self._pv is not None
        new_index = build_index(db)
        self.db = db
        self._timelines.clear()
        self._stat_files()
        if full:
            self.book = SpriteBook(db, self.game_dir)
            self._pv = None
            self._catalog = None
            self.index = new_index
            self._rebuild_tree()
        else:
            self._patch_lines(new_index)
        if cur_key is not None and cur_key[0] in db.labels:
            self._pv_frames = self._timeline(cur_key[0])
            self._pv_i = min(cur_key[1], len(self._pv_frames) - 1)
        else:
            self._pv_frames, self._pv_i = [], -1
        if full:
            if had_engine:
                self._pv_engine()
            if self._pv_visible():
                self._pv_show(animate=False)
            else:
                self._pv_dirty = True

    def _patch_lines(self, new_index) -> None:
        """Pone al día, en los objetos que ya muestra la lista, los nº de línea que cambiaron."""
        fresh = {i.name: i for infos in new_index.values() for i in infos}
        for infos in self.index.values():
            for info in infos:
                new = fresh.get(info.name)
                if new is None:
                    continue
                info.line = new.line
                for dl in info.lines:
                    if dl.n <= len(new.lines):
                        nl = new.lines[dl.n - 1]
                        dl.line, dl.who_var, dl.who, dl.attrs = nl.line, nl.who_var, nl.who, list(nl.attrs)
                        dl.text, dl.raw_text = nl.text, nl.raw_text      # por si se editó el diálogo
        for iid, dl in self._rows.items():
            self.rows.set(iid, "who", dl.speaker)
            self.rows.set(iid, "text", dl.text.replace("\n", " ⏎ "))
            self.rows.set(iid, "where", dl.where)

    def _rebuild_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._groups.clear()
        self.rows.delete(*self.rows.get_children())
        self._rows.clear()
        self._fill_tree()

    def _reload_from_disk(self) -> None:
        self._reload_db(full=True)
        self._pv_status.config(text="Scripts recargados desde el disco (tus ediciones pendientes se conservan).")
        self._update_ops()

    # --- auto-guardado (en segundo plano, con after(): no bloquea la ventana ni usa hilos)
    def _schedule_autosave(self) -> None:
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        if self._autosave.get() and self._is_dirty():
            self._autosave_job = self.after(AUTOSAVE_DELAY_MS, self._autosave_fire)

    def _autosave_fire(self) -> None:
        self._autosave_job = None
        if not self._autosave.get() or not self._is_dirty():
            return
        if self._modal or self._pv_job is not None:       # no guardar a mitad de un diálogo/animación
            self._autosave_job = self.after(1000, self._autosave_fire)
            return
        self.save(auto=True)

    # --- cerrar la ventana
    def _on_close(self) -> None:
        if self._is_dirty():
            dlg = _AskSave(self, self.hist.pending(self._pv_edits))
            self.wait_window(dlg)
            if dlg.choice == "cancel":
                return
            if dlg.choice == "save" and not self.save():
                return                                     # no se pudo guardar: la ventana sigue abierta
        for job in (self._autosave_job, self._pv_job):
            if job is not None:
                self.after_cancel(job)
        self.destroy()

    def _fill_tree(self) -> None:
        total = count_lines(self.index)
        n_labels = sum(len(v) for v in self.index.values())
        self.info.config(text=f"{len(self.index)} archivos · {n_labels} labels · {total} líneas de diálogo")
        for file, infos in self.index.items():
            all_lines = [dl for i in infos for dl in i.lines]
            fid = self.tree.insert("", "end", text=f"{file}  ({len(all_lines)})", open=False)
            self._groups[fid] = all_lines
            for info in infos:
                lid = self.tree.insert(fid, "end", text=f"{info.name}  ({len(info.lines)})")
                self._groups[lid] = info.lines

    # ------------------------------------------------------------------ eventos
    def _show(self, lines) -> None:
        self.rows.delete(*self.rows.get_children())
        self._rows.clear()
        for w in self.cells_frame.winfo_children():
            w.destroy()
        self._set_text(self.side_info, "")
        for dl in lines:
            iid = self.rows.insert("", "end", values=(
                dl.n, dl.label, dl.speaker, dl.text.replace("\n", " ⏎ "), dl.where))
            self._rows[iid] = dl
        self._set_detail("")
        first = self.rows.get_children()
        if first:                      # selecciona la primera línea para ver su pose enseguida
            self.rows.selection_set(first[0])

    def _on_select_group(self, _event=None) -> None:
        sel = self.tree.selection()
        if sel and sel[0] in self._groups:
            self._show(self._groups[sel[0]])

    def _on_select_row(self, _event=None) -> None:
        sel = self.rows.selection()
        if sel and sel[0] in self._rows:
            dl: DialogueLine = self._rows[sel[0]]
            pose = f"   pose: {' '.join(dl.attrs)}" if dl.attrs else ""
            self._set_detail(f"{dl.speaker}{pose}   ({dl.where})\n\n{dl.text}")
            self._render_poses(dl)
            self._pv_set(dl)

    # ------------------------------------------------------------------ editar el texto de los diálogos
    def _edit_dialogues(self) -> None:
        """F2 / doble clic / «Editar»: en Preview edita la línea que se ve; en Diálogos, la elegida
        (una sola -> editor por campos; varias -> editor de texto)."""
        if self._modal:
            return
        if self._pv_visible():
            self._edit_current_line()
            return
        dls = sorted((self._rows[i] for i in self.rows.selection() if i in self._rows),
                     key=lambda d: (d.file, d.line))
        if not dls:
            messagebox.showinfo("Editar diálogo", "Elige primero una o varias líneas de diálogo.", parent=self)
            return
        if len({d.file for d in dls}) > 1:
            messagebox.showinfo("Editar diálogo", "Elige líneas de un solo archivo a la vez.", parent=self)
            return
        if len(dls) == 1:
            self._edit_line(dls[0])
        else:
            self._edit_raw(dls)

    def _edit_current_line(self) -> None:
        """Edita la línea del diálogo que se ve en la pestaña Preview."""
        if self._modal:
            return
        frame = self._pv_frame()
        dl = self._dl_for_frame(frame) if frame is not None else None
        if dl is None:
            messagebox.showinfo("Editar línea", "Elige primero un diálogo en la pestaña «Diálogos».", parent=self)
            return
        self._edit_line(dl)

    def _dl_for_frame(self, frame):
        """La DialogueLine del índice que corresponde a un Frame del preview."""
        ev = frame.event
        for infos in self.index.values():
            for info in infos:
                if info.name != ev.label:
                    continue
                if frame.index < len(info.lines):
                    dl = info.lines[frame.index]
                    if dl.file == ev.file and dl.line == ev.line:
                        return dl
                return next((d for d in info.lines if d.file == ev.file and d.line == ev.line), None)
        return None

    def _read_code(self, dl) -> Optional[str]:
        try:
            return self.writer.read_statements(dl.file, [dl.line])[0][0]
        except ValueError as exc:
            messagebox.showerror("Editar diálogo", str(exc), parent=self)
            return None

    def _check_say(self, parts):
        """(error, línea) para unas SayParts: primero las reglas del editor, luego las del escritor."""
        why = say_edit.validate(parts, self.db.characters)
        if why:
            return why, ""
        code = say_edit.build_say(parts)
        return (rpy_writer.validate_say(code) or ""), code

    def _edit_line(self, dl) -> None:
        """Editor por campos de UNA línea: hablante · pose (opcional) · texto."""
        old = self._read_code(dl)
        if old is None:
            return
        parts = say_edit.parse_say(old)
        if parts is None:
            messagebox.showinfo(
                "Editar línea", "Esta línea no tiene la forma  personaje [pose] \"texto\" ; "
                "la abro en el editor de texto.", parent=self)
            self._edit_raw([dl], [old])
            return
        dlg = _SayEditor(self, dl, parts, say_edit.speaker_choices(self.db),
                         say_edit.temp_names(self.db), self._check_say)
        self._modal += 1
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        if dlg.result is None:
            return
        new = say_edit.parse_say(dlg.result)
        if new is not None and say_edit.same(new, parts):
            self._pv_status.config(text="Diálogo sin cambios.")
            return                                   # no se reescribe una línea que dice lo mismo
        self._apply_dialogue_edits([dl], [old], [dlg.result])

    def _edit_raw(self, dls, olds=None) -> None:
        """Editor de texto: las líneas tal como están en el script (una o varias)."""
        if olds is None:
            try:
                stmts = self.writer.read_statements(dls[0].file, [d.line for d in dls])
            except ValueError as exc:
                messagebox.showerror("Editar diálogo", str(exc), parent=self)
                return
            olds = [code for code, _c in stmts]
        dlg = _DialogueEditor(self, dls, olds, rpy_writer.validate_say)
        self._modal += 1
        try:
            self.wait_window(dlg)
        finally:
            self._modal -= 1
        if dlg.result is not None:
            self._apply_dialogue_edits(dls, olds, dlg.result)

    # --- textbox del preview: doble clic y selección rápida de hablante
    def _pv_textbox_span(self):
        """(y0, y1) del textbox + caja del nombre, en px de la imagen 1280x720."""
        if self._pv is None:
            return None
        g = self._pv.gui
        h = float(g.textbox_height)
        top = float(g.textbox_yalign) * (720 - h)
        return top - 40, top + h

    def _pv_in_textbox(self, event) -> bool:
        span = self._pv_textbox_span()
        ox, oy, sc = self._pv_geom
        if span is None or sc <= 0 or self._pv_img is None:
            return False
        ix, iy = (event.x - ox) / sc, (event.y - oy) / sc
        return 0 <= ix <= 1280 and span[0] <= iy <= span[1]

    def _pv_double(self, event):
        if self._pv_in_textbox(event):
            self._edit_current_line()
            return "break"
        return None

    def _pv_hover(self, event) -> None:
        if self._pv_press_info is None and self._pv_drag_src is None:
            self._pv_canvas.config(cursor="hand2" if self._pv_in_textbox(event) else "")

    def _tb_refresh(self, frame=None) -> None:
        """Panel Textbox: resumen de la línea y el hablante actual en el desplegable."""
        frame = frame or self._pv_frame()
        self._tb_choices = say_edit.quick_choices(self.db)
        self._tb_box.config(values=[c.label for c in self._tb_choices])
        if frame is None:
            self._tb_info.config(text="")
            self._tb_speaker.set("")
            return
        d = frame.event.data
        who = d["who"]
        if not who:
            cur = say_edit.NARRATOR_LABEL
        elif who in self.db.characters:
            cur = say_edit.speaker_label(self.db, who)
        elif who == "extend":
            cur = ""
        else:
            cur = f"“{who}” (temporal)"
        self._tb_speaker.set(cur)
        pose = f" [{' '.join(d['attrs'])}]" if d["attrs"] else ""
        text = d["plain"].replace("\n", " ⏎ ")
        if len(text) > 90:
            text = text[:87] + "…"
        name = d.get("who_name") or who or "(narrador)"
        temp = " (temporal)" if who and who != "extend" and who not in self.db.characters else ""
        self._tb_info.config(text=f"{name}{temp}{pose}:  {text}")

    def _tb_quick_speaker(self) -> None:
        """Cambia el hablante de la línea elegida desde el desplegable del Textbox."""
        if self._modal:
            return
        ch = next((c for c in self._tb_choices if c.label == self._tb_speaker.get()), None)
        frame = self._pv_frame()
        dl = self._dl_for_frame(frame) if frame is not None else None
        if ch is None or dl is None:
            self._tb_refresh()
            return
        if ch.kind == "new":
            self._modal += 1
            try:
                name = simpledialog.askstring("Nombre temporal", "Nombre que verá el jugador (p. ej. ???):",
                                              parent=self)
            finally:
                self._modal -= 1
            name = " ".join((name or "").split())
            if not name:
                self._tb_refresh()
                return
            ch = say_edit.Choice("temp", name, name)
        self._quick_apply(dl, ch)
        self._tb_refresh()

    def _quick_apply(self, dl, ch) -> None:
        old = self._read_code(dl)
        if old is None:
            return
        parts = say_edit.parse_say(old)
        if parts is None:
            messagebox.showinfo("Editar línea", "Esta línea tiene una forma especial: usa «Editar línea».",
                                parent=self)
            return
        if ((ch.kind == "var" and not parts.temp and parts.speaker == ch.value)
                or (ch.kind == "narr" and not parts.temp and not parts.speaker)
                or (ch.kind == "temp" and parts.temp and parts.speaker == ch.value)):
            return                                     # ya habla ese: no se toca (ni se pierde su pose)
        new = say_edit.with_speaker(parts, ch.kind, ch.value)
        why, code = self._check_say(new)
        if why:
            messagebox.showerror("Hablante", why, parent=self)
            return
        self._apply_dialogue_edits([dl], [old], [code])

    def _apply_dialogue_edits(self, dls, olds, news) -> bool:
        """Sobrescribe en el .rpy las líneas cuyo texto cambió y sincroniza lista, índice y preview."""
        edits = [rpy_writer.DialogueEdit((dl.label, dl.n), dl.file, dl.line, old, new.strip())
                 for dl, old, new in zip(dls, olds, news) if new.strip() != old.strip()]
        if not edits:
            self._pv_status.config(text="Diálogo sin cambios.")
            return True
        results = self.writer.apply_dialogues(edits, self._mtimes)
        errors = [r.msg for r in results if not r.ok]
        if any(r.ok for r in results):
            self._reload_db(full=False)            # texto y nº de línea de lo escrito
            if self._pv_visible():
                self._pv_show(animate=False)
            else:
                self._pv_dirty = True
            self._on_select_row()
            self._update_ops()
        if errors:
            messagebox.showerror("Editar diálogo", "\n\n".join(errors[:6]), parent=self)
            return False
        self._pv_status.config(text=f"{len(edits)} diálogo(s) actualizado(s) en el script · {time.strftime('%H:%M:%S')}")
        return True

    # ------------------------------------------------------------------ panel de poses
    def _frame_for(self, dl: DialogueLine):
        frames = self._timeline(dl.label)
        i = dl.n - 1
        if i < len(frames) and frames[i].event.file == dl.file and frames[i].event.line == dl.line:
            return frames[i]
        return next((f for f in frames if f.event.file == dl.file and f.event.line == dl.line), None)

    def _render_poses(self, dl: DialogueLine) -> None:
        for w in self.cells_frame.winfo_children():
            w.destroy()
        self._photos = []
        frame = self._frame_for(dl)
        if frame is None:
            self.side_title.config(text="Pose en escena (no disponible)")
            self._set_text(self.side_info, "")
            return
        views = build_cell_views(frame, self.db, self.book)
        stage = frame.stage
        n = max(len(views), 1)
        size = max(90, min(340, 460 // n))
        self.side_title.config(
            text=f"Fondo: {stage.bg or '-'}   ·   {len(views)} celda(s)   ·   habla: {stage.speaker or '(narrador)'}")
        infos = []
        if not HAVE_PIL:
            infos.append("Para ver los sprites instala Pillow:  py -3 -m pip install pillow")
        for col, v in enumerate(views):
            cell = ttk.Frame(self.cells_frame, padding=2)
            cell.grid(row=0, column=col, sticky="n", padx=3)
            tk.Label(cell, text=("🗣 " if v.speaking else "") + v.title, wraplength=size,
                     font=("TkDefaultFont", 9, "bold" if v.speaking else "normal")).pack()
            if v.image is not None and HAVE_TKIMG:
                res = getattr(Image, "Resampling", Image).LANCZOS
                photo = ImageTk.PhotoImage(v.image.resize((size, size), res))
                self._photos.append(photo)
                tk.Label(cell, image=photo, borderwidth=1, relief="solid").pack()
            else:
                cv = tk.Canvas(cell, width=size, height=size, highlightthickness=0)
                cv.create_rectangle(2, 2, size - 2, size - 2, dash=(4, 3))
                cv.create_text(size // 2, size // 2, text="vacía" if not v.image_name else "sin imagen")
                cv.pack()
            if v.info:
                infos.append(v.info)
        self._set_text(self.side_info, "\n\n".join(infos))

    def _set_text(self, widget, text: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")

    def _on_search(self) -> None:
        q = self.query.get()
        if not q.strip():
            return self._clear_search()
        found = search(self.index, q)
        self.tree.selection_remove(self.tree.selection())
        self._show(found)
        self.info.config(text=f"{len(found)} resultados para «{q.strip()}»")

    def _clear_search(self) -> None:
        self.query.set("")
        self.rows.delete(*self.rows.get_children())
        self._rows.clear()
        self._set_detail("")
        total = count_lines(self.index)
        n_labels = sum(len(v) for v in self.index.values())
        self.info.config(text=f"{len(self.index)} archivos · {n_labels} labels · {total} líneas de diálogo")

    def _set_detail(self, text: str) -> None:
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")


class _AskSave(tk.Toplevel):
    """Aviso al cerrar con cambios sin guardar. `choice` = "save" | "discard" | "cancel"."""

    def __init__(self, master, n: int):
        super().__init__(master)
        self.title("Cambios sin guardar")
        self.transient(master)
        self.resizable(False, False)
        self.choice = "cancel"
        frm = ttk.Frame(self, padding=16)
        frm.pack()
        ttk.Label(frm, justify="left", text=(
            f"Hay {n} diálogo(s) con cambios sin guardar en tus scripts .rpy.\n"
            "¿Quieres guardarlos antes de salir?")).pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(anchor="e", pady=(14, 0))
        ok = ttk.Button(row, text="Guardar", command=lambda: self._done("save"))
        ok.pack(side="left")
        ttk.Button(row, text="No guardar", command=lambda: self._done("discard")).pack(side="left", padx=6)
        ttk.Button(row, text="Cancelar", command=lambda: self._done("cancel")).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", lambda: self._done("cancel"))
        self.bind("<Return>", lambda e: self._done("save"))
        self.bind("<Escape>", lambda e: self._done("cancel"))
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        ok.focus_set()
        self.grab_set()

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.destroy()


_BG = "#3a3a3a"
_SEL = "#ffd400"


class _DialogueEditor(tk.Toplevel):
    """Edita las líneas de diálogo elegidas tal como están escritas en el script (una por línea).

    Al aceptar, `result` = lista con el texto nuevo de cada línea (mismo nº de líneas que se abrieron:
    aquí solo se reescribe lo que ya existe; no se añaden ni se quitan diálogos)."""

    def __init__(self, master, dls, codes, validate):
        super().__init__(master)
        self.title("Editar diálogo" if len(codes) == 1 else f"Editar {len(codes)} diálogos")
        self.transient(master)
        self.result = None
        self._n = len(codes)
        self._orig = list(codes)
        self._validate = validate
        where = f"{dls[0].file}:{dls[0].line}" + (f" … {dls[-1].line}" if len(dls) > 1 else "")
        ttk.Label(self, text=f"{dls[0].label}  ·  {where}", font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w", padx=10, pady=(10, 2))
        ttk.Label(self, foreground="#666666", justify="left", wraplength=760, text=(
            'Una línea por diálogo, tal como va en el script:  personaje [pose] "texto"  [with ...]\n'
            'Comillas dentro del texto: \\"   ·   salto de línea: \\n   ·   no añadas ni quites líneas. '
            "Tu comentario # de cada línea se conserva.")).pack(anchor="w", padx=10)
        self.txt = tk.Text(self, width=110, height=min(max(self._n + 1, 5), 22), wrap="none", undo=True,
                           font=("Courier New", 10))
        self.txt.pack(fill="both", expand=True, padx=10, pady=8)
        self.txt.insert("1.0", "\n".join(codes))
        self.txt.edit_reset()
        self.msg = ttk.Label(self, text="", foreground="#b00000", wraplength=760, justify="left")
        self.msg.pack(anchor="w", padx=10)
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=10)
        ttk.Button(btns, text="Restaurar texto", command=self._reset).pack(side="left")
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Aplicar al script  (Ctrl+Enter)", command=self._ok).pack(side="right", padx=6)
        self.txt.bind("<KeyRelease>", lambda e: self._check())
        self.bind("<Control-Return>", lambda e: (self._ok(), "break")[1])
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.txt.focus_set()

    def _lines(self):
        return self.txt.get("1.0", "end-1c").split("\n")

    def _problems(self):
        lines = self._lines()
        if len(lines) != self._n:
            return [f"Debe haber exactamente {self._n} línea(s), una por diálogo (hay {len(lines)})."]
        out = []
        for k, (old, new) in enumerate(zip(self._orig, lines), 1):
            if new.strip() != old.strip():
                why = self._validate(new)
                if why:
                    out.append(f"Línea {k}: {why}.")
        return out

    def _check(self) -> bool:
        problems = self._problems()
        self.msg.config(text="\n".join(problems[:4]))
        return not problems

    def _reset(self) -> None:
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(self._orig))
        self._check()

    def _ok(self) -> None:
        if not self._check():
            return
        self.result = [l.strip() for l in self._lines()]
        self.destroy()


class _SayEditor(tk.Toplevel):
    """Editor por campos de UNA línea de diálogo.

        Quién habla (izquierda)                       Texto del diálogo (derecha)
        ├ Hablante  [selector o texto]                ┌───────────────────────────┐
        ├ ☐ Hablante no definido / nombre temporal    │ ...                       │
        └ Pose / expresión (opcional)                 └───────────────────────────┘

    Con «nombre temporal» se escribe  "Nombre" "texto"  (sin Character ni pose). `result` = la línea
    nueva (str) al aplicar, o None."""

    def __init__(self, master, dl, parts, choices, temp_names, check):
        super().__init__(master)
        self.title(f"Editar línea · {dl.label} · {dl.where}")
        self.transient(master)
        self.result = None
        self._p0 = parts
        self._check = check
        self._by_label = {c.label: c.value for c in choices if c.kind == "var"}
        self._label_of = {c.value: c.label for c in choices if c.kind == "var"}
        self._narr = say_edit.NARRATOR_LABEL

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(outer, text="Quién habla", padding=8)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(left, text="Hablante:").pack(anchor="w")
        self.v_who = tk.StringVar(value=self._initial_who(parts))
        self.cb_who = ttk.Combobox(left, textvariable=self.v_who, width=30,
                                   values=[c.label for c in choices if c.kind in ("narr", "var")])
        self.cb_who.pack(fill="x", pady=(2, 6))
        self.v_temp = tk.BooleanVar(value=parts.temp)
        ttk.Checkbutton(left, text="Hablante no definido / nombre temporal", variable=self.v_temp,
                        command=self._on_temp).pack(anchor="w")
        self.hint = ttk.Label(left, text="", wraplength=250, justify="left", foreground="#666666")
        self.hint.pack(anchor="w", pady=(2, 8))
        self.cb_temp = None
        if temp_names:
            ttk.Label(left, text="Temporales que ya usa el script:").pack(anchor="w")
            self.cb_temp = ttk.Combobox(left, state="readonly", width=30, values=list(temp_names))
            self.cb_temp.pack(fill="x", pady=(2, 8))
            self.cb_temp.bind("<<ComboboxSelected>>", self._on_pick_temp)
        ttk.Label(left, text="Pose / expresión (opcional):").pack(anchor="w")
        self.v_pose = tk.StringVar(value=parts.pose)
        self.ent_pose = ttk.Entry(left, textvariable=self.v_pose, width=32)
        self.ent_pose.pack(fill="x", pady=(2, 0))
        ttk.Label(left, text="Vacío = no cambia la pose en esta línea.\nEjemplos:  1a   ·   2b", wraplength=250,
                  justify="left", foreground="#666666").pack(anchor="w", pady=(2, 0))

        right = ttk.LabelFrame(outer, text="Texto del diálogo", padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        sb = ttk.Scrollbar(right, orient="vertical")
        sb.pack(side="right", fill="y")
        self.txt = tk.Text(right, width=64, height=10, wrap="word", undo=True, yscrollcommand=sb.set,
                           font=("TkDefaultFont", 11))
        sb.config(command=self.txt.yview)
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.insert("1.0", parts.text)
        self.txt.edit_reset()

        ttk.Label(outer, text="Se escribirá en el script:").grid(row=1, column=0, columnspan=2, sticky="w",
                                                                 pady=(10, 0))
        self.lbl_code = tk.Label(outer, text="", anchor="w", justify="left", wraplength=820,
                                 font=("Courier New", 10), bg="#f2f2f2", relief="sunken", padx=6, pady=3)
        self.lbl_code.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.msg = ttk.Label(outer, text="", foreground="#b00000", wraplength=820, justify="left")
        self.msg.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        btns = ttk.Frame(outer)
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="right")
        self.btn_ok = ttk.Button(btns, text="Aplicar al script  (Ctrl+Enter)", command=self._ok)
        self.btn_ok.pack(side="right", padx=6)

        for var in (self.v_who, self.v_pose):
            var.trace_add("write", lambda *a: self._refresh())
        self.cb_who.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        self.txt.bind("<KeyRelease>", lambda e: self._refresh())
        self.bind("<Control-Return>", lambda e: (self._ok(), "break")[1])
        self.bind("<Escape>", lambda e: self.destroy())
        self._refresh()
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.txt.focus_set()

    def _initial_who(self, p) -> str:
        if p.temp:
            return p.speaker
        if not p.speaker:
            return self._narr
        return self._label_of.get(p.speaker, p.speaker)

    def _collect(self):
        """Las SayParts que describen lo que hay ahora mismo en los campos."""
        temp = bool(self.v_temp.get())
        who = self.v_who.get().strip()
        if temp:
            speaker = who
        elif who in ("", self._narr):
            speaker = ""
        else:
            speaker = self._by_label.get(who, who)
        pose = "" if (temp or not speaker) else self.v_pose.get().strip()
        return say_edit.SayParts(speaker=speaker, temp=temp, pose=pose, text=self.txt.get("1.0", "end-1c"),
                                 tail=self._p0.tail, raw_text=self._p0.raw_text, orig_text=self._p0.orig_text)

    def _on_temp(self) -> None:
        who = self.v_who.get().strip()
        if self.v_temp.get():
            if who == self._narr or who in self._by_label:
                self.v_who.set("")                       # el nombre temporal se escribe a mano
            self.cb_who.focus_set()
        elif who not in self._by_label and who != self._narr:
            self.v_who.set(self._narr)
        self._refresh()

    def _on_pick_temp(self, _e=None) -> None:
        self.v_temp.set(True)
        self.v_who.set(self.cb_temp.get())
        self._refresh()

    def _refresh(self) -> bool:
        parts = self._collect()
        why, code = self._check(parts)
        narr = not parts.temp and not parts.speaker
        self.ent_pose.config(state="disabled" if (parts.temp or narr) else "normal")
        self.hint.config(text=('Se escribe:  "Nombre" "Diálogo".  Sin pose.' if parts.temp else
                               ("Sin hablante: línea de narrador." if narr else "")))
        self.lbl_code.config(text=code or "—")
        self.msg.config(text=why)
        self.btn_ok.state(["disabled"] if why else ["!disabled"])
        return not why

    def _ok(self) -> None:
        if not self._refresh():
            return
        self.result = self._check(self._collect())[1]
        self.destroy()


def _photo(img, w: int, h: int):
    """PIL.Image -> PhotoImage que cabe en w x h conservando la proporción (None si no hay imagen)."""
    if img is None or not HAVE_TKIMG:
        return None
    s = min(w / img.size[0], h / img.size[1])
    size = (max(1, int(img.size[0] * s)), max(1, int(img.size[1] * s)))
    return ImageTk.PhotoImage(img.resize(size, getattr(Image, "Resampling", Image).LANCZOS))


class _Strip(ttk.LabelFrame):
    """Selector de miniaturas de 1 fila x 3 columnas, con ◀ ▶ para pasar de 3 en 3."""
    COLS = 3

    def __init__(self, master, title: str, box, on_pick):
        super().__init__(master, text=title, padding=(6, 4))
        self.box = box
        self.on_pick = on_pick
        self.items = []                   # [(clave, texto)]
        self.thumb = lambda key: None     # clave -> PhotoImage | None
        self.sel = None
        self.page = 0
        self._photos = []
        self._cells = []
        self.btn_prev = ttk.Button(self, text="◀", width=3, command=lambda: self._turn(-1))
        self.btn_next = ttk.Button(self, text="▶", width=3, command=lambda: self._turn(1))
        row = ttk.Frame(self)
        self.info = ttk.Label(self, text="")
        self.btn_prev.grid(row=0, column=0)
        row.grid(row=0, column=1, padx=4)
        self.btn_next.grid(row=0, column=2)
        self.info.grid(row=1, column=0, columnspan=3, pady=(2, 0))
        for c in range(self.COLS):
            fr = tk.Frame(row, width=box[0] + 14, height=box[1] + 32, bg=_BG, cursor="hand2",
                          highlightthickness=3, highlightbackground=_BG, highlightcolor=_BG)
            fr.pack_propagate(False)
            fr.grid(row=0, column=c, padx=3)
            txt = tk.Label(fr, bg=_BG, fg="#ffffff", font=("TkDefaultFont", 9, "bold"))
            txt.pack(side="bottom")
            img = tk.Label(fr, bg=_BG, fg="#aaaaaa")
            img.pack(expand=True)
            for w in (fr, img, txt):
                w.bind("<Button-1>", lambda e, c=c: self._click(c))
            self._cells.append((fr, img, txt))

    def set_items(self, items, thumb, sel=None) -> None:
        self.items, self.thumb, self.sel = list(items), thumb, sel
        keys = [k for k, _t in self.items]
        self.page = (keys.index(sel) // self.COLS) if sel in keys else 0
        self.refresh()

    def refresh(self) -> None:
        self._photos = []
        start = self.page * self.COLS
        for c, (fr, img, txt) in enumerate(self._cells):
            i = start + c
            if i < len(self.items):
                key, label = self.items[i]
                ph = self.thumb(key)
                self._photos.append(ph)
                img.config(image=ph if ph is not None else "", text="" if ph is not None else "sin\nimagen")
                txt.config(text=label)
                color = _SEL if key == self.sel else _BG
            else:
                img.config(image="", text="")
                txt.config(text="")
                color = _BG
            fr.config(highlightbackground=color, highlightcolor=color)
        pages = max(1, -(-len(self.items) // self.COLS))
        self.info.config(text=f"{self.page + 1} / {pages}   ·   {len(self.items)} en total")
        self.btn_prev.state(["!disabled"] if self.page > 0 else ["disabled"])
        self.btn_next.state(["!disabled"] if self.page < pages - 1 else ["disabled"])

    def _turn(self, d: int) -> None:
        pages = max(1, -(-len(self.items) // self.COLS))
        self.page = max(0, min(self.page + d, pages - 1))
        self.refresh()

    def _click(self, c: int) -> None:
        i = self.page * self.COLS + c
        if i < len(self.items):
            self.sel = self.items[i][0]
            self.refresh()
            self.on_pick(self.sel)


class _PosePicker(tk.Toplevel):
    """Selector en dos pasos. Al aceptar: `result` = cells.Placement y `transform` = 't21'...

    Paso 1: los personajes, con su sprite de portada (pose 1).
    Paso 2: rostros (los .png de cara) y poses (cuerpo) en tiras de 1 fila x 3 columnas, el
            efecto/transform (`at`; pestaña «Efectos» o «Avanzado»), el zorder y el código `show` en vivo.
    Si la celda ya tiene a alguien, se abre directamente en el paso 2.
    """

    def __init__(self, master, catalog, transforms, current=None, default_transform="t11",
                 known=None, title="Elegir personaje", db=None, engine=None, count=0, slot=1, bg=""):
        super().__init__(master)
        self._db, self._engine, self._count, self._slot, self._bg = db, engine, count, slot, bg
        self._effects = tfcatalog.effects(db) if db is not None else []
        self._syncing = False
        self._pvw = None
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.cat = catalog
        self.result = None
        self.transform = ""
        self.reflow = True
        self._known = set(known or ())
        self._photos = []
        self._cache = {}                    # miniaturas ya generadas (PhotoImage)
        self.pose = ""
        self.expr = ""
        self.tag = tk.StringVar(value=current.tag if current else "")
        self.z = tk.StringVar(value=str(current.zorder if current else 0))
        self.tf = tk.StringVar(value=default_transform)
        self.step1 = ttk.Frame(self, padding=10)
        self.step2 = ttk.Frame(self, padding=10)
        self._build_step1()
        self._build_step2()
        self.bind("<Escape>", lambda e: self.destroy())
        if current is not None and current.tag in self.cat.tags():
            self._go_step2(current.tag, current.attrs)
        else:
            self.step1.pack()
        self.grab_set()

    # ------------------------------------------------------------------ paso 1: personaje
    def _build_step1(self) -> None:
        ttk.Label(self.step1, text="Elige un personaje", font=("TkDefaultFont", 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        for i, tag in enumerate(self.cat.tags()):
            attrs = self.cat.default_preview(tag)
            photo = _photo(self.cat.image(tag, attrs), 150, 190) if attrs else None
            self._photos.append(photo)
            label = tag.capitalize() + (f"\n({tag} {' '.join(attrs)})" if attrs else "")
            tk.Button(self.step1, text=label, image=photo, compound="top", padx=6, pady=6,
                      command=lambda t=tag: self._go_step2(t)).grid(row=1 + i // 4, column=i % 4, padx=4, pady=4)
        ttk.Button(self.step1, text="Cancelar", command=self.destroy).grid(
            row=99, column=3, sticky="e", pady=(10, 0))

    # ------------------------------------------------------------------ paso 2: rostro, pose, at, z
    def _build_step2(self) -> None:
        f = self.step2
        self.head = ttk.Label(f, text="", font=("TkDefaultFont", 11, "bold"))
        self.head.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        pf = tk.Frame(f, width=250, height=330, bg=_BG)
        pf.pack_propagate(False)
        pf.grid(row=1, column=0, rowspan=5, padx=(0, 10), sticky="n")
        self.prev = tk.Label(pf, bg=_BG, fg="#ffffff")
        self.prev.pack(expand=True)
        self.strip_face = _Strip(f, "Rostro (expresión)", (110, 100), self._pick_expr)
        self.strip_pose = _Strip(f, "Pose (cuerpo)", (110, 150), self._pick_pose)
        self.strip_face.grid(row=1, column=1, sticky="w")
        self.strip_pose.grid(row=2, column=1, sticky="w", pady=(6, 0))
        opt = ttk.Frame(f)
        opt.grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Label(opt, text="Z (capa):").pack(side="left")
        tk.Spinbox(opt, from_=-99, to=99, width=4, textvariable=self.z,
                   command=self._update).pack(side="left", padx=4)
        ttk.Label(opt, text="mayor = al frente").pack(side="left")
        self.z.trace_add("write", lambda *_: self._update_code())
        # ---- transform: pestaña «Efectos» (lista corta, sin t11/t22...) y pestaña «Avanzado» (texto)
        self.tfnb = ttk.Notebook(f)
        self.tfnb.grid(row=4, column=1, sticky="we", pady=(8, 0))
        fx = ttk.Frame(self.tfnb, padding=6)
        self.tfnb.add(fx, text="Efectos")
        sbf = ttk.Scrollbar(fx, orient="vertical")
        sbf.pack(side="left", fill="y")                      # la barra se empaqueta antes que la lista
        self.fx_list = tk.Listbox(fx, height=8, width=22, exportselection=False, yscrollcommand=sbf.set)
        sbf.config(command=self.fx_list.yview)
        self.fx_list.pack(side="left")
        for e in self._effects:
            self.fx_list.insert("end", e.label + ("" if e.known else "   · propio"))
        self.fx_list.bind("<<ListboxSelect>>", self._on_effect)
        side = ttk.Frame(fx)
        side.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.fx_help = ttk.Label(side, text="", wraplength=250, justify="left")
        self.fx_help.pack(anchor="w")
        ttk.Button(side, text="▶ Previsualizar animación…", command=self._open_preview).pack(anchor="w", pady=(10, 0))
        adv = ttk.Frame(self.tfnb, padding=6)
        self.tfnb.add(adv, text="Avanzado (texto)")
        ttk.Label(adv, justify="left", text=(
            "Transform tal como va tras «at» (texto original). Escribe lo que quieras:\n"
            "  t21   ·   hf32   ·   t21, hop   ·   hop(x=400, z=0.9)   ·   mi_pos")).pack(anchor="w")
        ttk.Entry(adv, textvariable=self.tf, font=("Courier New", 10), width=54).pack(anchor="w", pady=(6, 0))
        self.reflow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(adv, text="Reajustar a los demás personajes si es una posición (t21, hf32...)",
                        variable=self.reflow_var).pack(anchor="w", pady=(6, 0))
        ttk.Button(adv, text="▶ Previsualizar animación…", command=self._open_preview).pack(anchor="w", pady=(6, 0))
        self.tf.trace_add("write", lambda *_: self._on_tf_changed())
        self.code = tk.Entry(f, font=("Courier New", 10), state="readonly", width=48,
                             readonlybackground="#f2f2f2")
        self.code.grid(row=5, column=1, sticky="we", pady=(8, 0))
        self.tf_msg = ttk.Label(f, text="", wraplength=380, justify="left")
        self.tf_msg.grid(row=6, column=1, sticky="w")
        btns = ttk.Frame(f)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="◀ Personajes", command=self._back).pack(side="left", padx=(0, 40))
        ttk.Button(btns, text="Cancelar", command=self.destroy).pack(side="left")
        ttk.Button(btns, text="Aceptar", command=self._ok).pack(side="left", padx=6)

    # ------------------------------------------------------------------ efectos <-> texto
    def _defined(self) -> set:
        return set(self._known)

    def _on_effect(self, _e=None) -> None:
        sel = self.fx_list.curselection()
        if not sel:
            return
        eff = self._effects[sel[0]]
        text = tfcatalog.resolve(eff, self._count, self._slot, self._defined())
        self._syncing = True
        try:
            self.tf.set(text)
        finally:
            self._syncing = False
        self._show_effect_help(eff, text)

    def _show_effect_help(self, eff, text: str) -> None:
        self.fx_help.config(text=f"{eff.help}\n\nSe escribe:  at {text}" if eff else "")

    def _on_tf_changed(self) -> None:
        """El texto cambió (a mano o por la lista): actualiza código, selección de la lista y ventana."""
        if not self._syncing:
            eff = tfcatalog.effect_of(self.tf.get(), self._db) if self._db is not None else None
            plain = eff is not None and tfcatalog.resolve(
                eff, self._count, self._slot, self._defined()) == self.tf.get().strip()
            self.fx_list.selection_clear(0, "end")
            if plain:
                i = self._effects.index(eff)
                self.fx_list.selection_set(i)
                self.fx_list.see(i)
                self._show_effect_help(eff, self.tf.get().strip())
            else:
                self.fx_help.config(text="Transform personalizado (se edita en la pestaña «Avanzado»).\n\n"
                                         f"Se escribe:  at {self.tf.get().strip()}")
        self._update_code()
        if self._pvw is not None and self._pvw.winfo_exists():
            self._pvw.refresh()

    def _open_preview(self) -> None:
        if self._engine is None:
            messagebox.showinfo("Previsualizar", "Hace falta Pillow para dibujar la animación:\n"
                                "py -3 -m pip install pillow", parent=self)
            return
        if self._pvw is not None and self._pvw.winfo_exists():
            self._pvw.lift()
            self._pvw.refresh()
            return
        self._pvw = _TransformPreview(self, self._engine, self.tag.get, self._attrs, self.tf.get,
                                      self._count, self._slot, self._bg)

    def _go_step2(self, tag, attrs=None) -> None:
        self.tag.set(tag)
        self.head.config(text=f"{tag.capitalize()}: elige rostro, pose y efecto")
        poses = self.cat.poses(tag)
        loc = self.cat.locate(tag, attrs) if attrs is not None else None
        if loc:
            self.pose, self.expr = loc
        else:
            self.pose = "1" if "1" in poses else (poses[0] if poses else "")
            exprs = self.cat.exprs(tag, self.pose)
            self.expr = "a" if "a" in exprs else (exprs[0] if exprs else "")
        self._cache.clear()
        self.strip_pose.set_items([(p, f"{p} · {cells.outfit_label(p)}") for p in poses],
                                  self._pose_thumb, self.pose)
        self._reload_faces()
        self._on_tf_changed()
        self.step1.pack_forget()
        self.step2.pack()
        self.bind("<Return>", lambda e: self._ok())

    def _back(self) -> None:
        self.step2.pack_forget()
        self.step1.pack()
        self.unbind("<Return>")

    def _reload_faces(self) -> None:
        tag = self.tag.get()
        exprs = self.cat.exprs(tag, self.pose)
        if self.expr not in exprs:
            self.expr = exprs[0] if exprs else ""
        self.strip_face.set_items([(e, e or "(única)") for e in exprs], self._face_thumb, self.expr)
        self._update()

    def _pick_pose(self, pose) -> None:
        self.pose = pose
        self._reload_faces()
        self.strip_pose.refresh()           # por si la expresión cambió (no todas las poses tienen todas)

    def _pick_expr(self, expr) -> None:
        self.expr = expr
        self.strip_pose.refresh()           # la pose se ve con el rostro elegido
        self._update()

    # ------------------------------------------------------------------ miniaturas
    def _face_thumb(self, expr):
        key = ("f", self.tag.get(), self.pose, expr)
        if key not in self._cache:
            tag = self.tag.get()
            img = self.cat.face_image(tag, self.pose, expr) or self.cat.sprite_image(tag, self.pose, expr)
            self._cache[key] = _photo(img, 110, 100)
        return self._cache[key]

    def _pose_thumb(self, pose):
        tag = self.tag.get()
        expr = self.expr if self.cat.combo(tag, pose, self.expr) else (
            (self.cat.exprs(tag, pose) or [""])[0])
        key = ("p", tag, pose, expr)
        if key not in self._cache:
            self._cache[key] = _photo(self.cat.sprite_image(tag, pose, expr), 110, 150)
        return self._cache[key]

    # ------------------------------------------------------------------ vista previa y código
    def _attrs(self):
        return self.cat.attrs(self.tag.get(), self.pose, self.expr)

    def _zorder(self) -> int:
        try:
            return max(-99, min(99, int(self.z.get())))
        except ValueError:
            return 0

    def _update(self) -> None:
        img = self.cat.sprite_image(self.tag.get(), self.pose, self.expr)
        photo = _photo(img, 240, 320)
        self._last = photo                    # Tk descarta la imagen si no se guarda
        self.prev.config(image=photo if photo is not None else "",
                         text="" if photo is not None else "(sin vista previa:\nfaltan los PNG o Pillow)")
        self._update_code()

    def _check_transform(self):
        """(lista de nombres | None, aviso). Solo es error lo que no se puede escribir tras `at`."""
        try:
            names = cells.parse_transform_list(self.tf.get())
        except ValueError as exc:
            self.tf_msg.config(text=str(exc), foreground="#b00000")
            return None
        unknown = [cells.call_name(n) for n in names if self._known and cells.call_name(n) not in self._known]
        if unknown:
            self.tf_msg.config(text="No está en tu transforms.rpy: " + ", ".join(unknown) +
                               " (el preview usará la posición estándar; el script se guarda igual).",
                               foreground="#a05000")
        else:
            self.tf_msg.config(text="", foreground="#666666")
        return names

    def _update_code(self) -> None:
        attrs = self._attrs()
        self._check_transform()
        text = f"show {self.tag.get()} {' '.join(attrs)} zorder {self._zorder()} at {self.tf.get().strip()}" if attrs else ""
        self.code.config(state="normal")
        self.code.delete(0, "end")
        self.code.insert(0, text)
        self.code.config(state="readonly")

    def _ok(self) -> None:
        attrs = self._attrs()
        if attrs is None:
            return
        if self._check_transform() is None:
            return                                   # transform imposible de escribir: no se cierra
        self.result = cells.Placement(self.tag.get(), tuple(attrs), self._zorder())
        self.transform = self.tf.get().strip()
        self.reflow = bool(self.reflow_var.get())
        self.destroy()


class _TransformPreview(tk.Toplevel):
    """Ventana dedicada para ver, en vivo, la animación de un transform aplicada a un personaje.

    Dibuja SOLO a ese personaje (con la pose/rostro elegidos) sobre el fondo de la escena, usando el
    mismo motor ATL que el preview principal. Se puede reproducir la entrada (`show`), el cambio de
    posición (`replace`) o la salida (`hide`), en bucle, a distinta velocidad o arrastrando la barra de
    tiempo. Se actualiza sola si cambias el texto del transform en el selector.
    """
    VIEW = (640, 360)
    LOOP_SECONDS = 4.0                  # un bucle infinito (panic...) se enseña durante estos segundos
    SPEEDS = {"0.25×": 0.25, "0.5×": 0.5, "1×": 1.0, "2×": 2.0}

    def __init__(self, master, engine, get_tag, get_attrs, get_expr, count: int, slot: int, bg: str = ""):
        super().__init__(master)
        self.title("Previsualizar animación")
        self.transient(master)
        self.resizable(False, False)
        self.eng, self.get_tag, self.get_attrs, self.get_expr = engine, get_tag, get_attrs, get_expr
        self.count, self.slot, self.bg = max(count, 1), max(slot, 1), bg
        self._job = None
        self._t0 = 0.0
        self._photo = None
        self._setting = False
        self._span = 0.0
        self.event = tk.StringVar(value="show")
        self.loop = tk.BooleanVar(value=False)
        self.speed = tk.StringVar(value="1×")
        self.src = tk.StringVar(value=str(self.slot % self.count + 1 if self.count > 1 else 1))
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        for val, txt in (("show", "Entrada"), ("replace", "Cambio de celda"), ("hide", "Salida")):
            ttk.Radiobutton(top, text=txt, value=val, variable=self.event,
                            command=self.play).pack(side="left", padx=4)
        ttk.Label(top, text="  desde la celda").pack(side="left")
        ttk.Combobox(top, textvariable=self.src, values=[str(i) for i in range(1, self.count + 1)],
                     state="readonly", width=3).pack(side="left", padx=4)
        self.src.trace_add("write", lambda *_: self.play())
        self.canvas = tk.Canvas(self, width=self.VIEW[0], height=self.VIEW[1], bg="black", highlightthickness=0)
        self.canvas.pack(padx=8)
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="▶ Reproducir", command=self.play).pack(side="left")
        ttk.Button(bar, text="■ Parar", command=self.stop).pack(side="left", padx=4)
        ttk.Checkbutton(bar, text="Bucle", variable=self.loop, command=self.play).pack(side="left", padx=6)
        ttk.Label(bar, text="Velocidad").pack(side="left", padx=(8, 2))
        cb = ttk.Combobox(bar, textvariable=self.speed, values=list(self.SPEEDS), state="readonly", width=6)
        cb.pack(side="left")
        self.clock = ttk.Label(bar, text="")
        self.clock.pack(side="right")
        self.scale = ttk.Scale(self, from_=0.0, to=1.0, orient="horizontal", command=self._on_scale)
        self.scale.pack(fill="x", padx=8)
        self.info = ttk.Label(self, text="", wraplength=620, justify="left", foreground="#a05000")
        self.info.pack(fill="x", padx=8, pady=(4, 8))
        self.bind("<space>", lambda e: self.play())
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    # ------------------------------------------------------------------ datos
    def _expr(self) -> str:
        return (self.get_expr() or "").strip()

    def _start_expr(self) -> str:
        return f"t{self.count}{self.src.get()}" if self.count else "t11"

    def _event(self) -> str:
        return self.event.get()

    def _duration(self) -> float:
        lib = self.eng.lib
        ev = self._event()
        dur = lib.duration_expr(self._expr(), ev)
        if lib.loops_expr(self._expr(), ev):
            return self.LOOP_SECONDS
        return dur

    # ------------------------------------------------------------------ reproducción
    def refresh(self) -> None:
        """Tras cambiar el texto del transform / la pose: vuelve a empezar."""
        self.play()

    def stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def close(self) -> None:
        self.stop()
        self.destroy()

    def play(self) -> None:
        self.stop()
        try:
            cells.parse_transform_list(self._expr())
        except ValueError as exc:
            self.info.config(text=str(exc))
            return
        self._span = self._duration()
        self._setting = True
        try:
            self.scale.config(to=max(self._span, 0.01))
            self.scale.set(0.0)
        finally:
            self._setting = False
        self._t0 = time.monotonic()
        self._tick()

    def _speed(self) -> float:
        return self.SPEEDS.get(self.speed.get(), 1.0)

    def _tick(self) -> None:
        el = (time.monotonic() - self._t0) * self._speed()
        if el >= self._span:
            self._draw(None if self._span <= 0 or self._event() != "hide" else self._span)
            self._set_scale(self._span)
            self._job = self.after(600, self.play) if self.loop.get() else None
            return
        self._draw(el)
        self._set_scale(el)
        self._job = self.after(30, self._tick)

    def _set_scale(self, t: float) -> None:
        self._setting = True
        try:
            self.scale.set(t)
        finally:
            self._setting = False
        self.clock.config(text=f"{t:0.2f} / {self._span:0.2f} s")

    def _on_scale(self, value) -> None:
        if self._setting:
            return
        self.stop()                                   # arrastrar la barra = ir a ese instante
        t = float(value)
        self._draw(t)
        self.clock.config(text=f"{t:0.2f} / {self._span:0.2f} s")

    def _draw(self, t) -> None:
        expr = self._expr()
        try:
            img = self.eng.sprite_frame(self.get_tag(), self.get_attrs() or (), expr, self._event(), t,
                                        self._start_expr(), self.bg, quick=True)
            res = getattr(Image, "Resampling", Image)
            self._photo = ImageTk.PhotoImage(img.resize(self.VIEW, res.BILINEAR))
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        except Exception as exc:                      # no tumbar la ventana por un sprite que falte
            self.info.config(text=f"No se pudo dibujar: {exc}")
            return
        notes = list(self.eng.warnings)
        unknown = self.eng.lib.unknown_in(expr)
        if unknown:
            notes.append("No está en tu transforms.rpy: " + ", ".join(unknown) + " (no se anima).")
        if self._span <= 0:
            notes.append("Este transform no tiene animación en este evento (aparece ya en su sitio).")
        elif self.eng.lib.loops_expr(expr, self._event()):
            notes.append(f"Bucle infinito: se enseñan {self.LOOP_SECONDS:g} s.")
        self.info.config(text="  ·  ".join(notes))


def run(db, game_dir: str) -> None:
    DialogueBrowser(db, game_dir).mainloop()
