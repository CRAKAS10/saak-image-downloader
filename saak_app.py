"""SAAK Image Downloader — Desktop GUI (Tkinter, Arabic)."""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from saak_engine import Crawler, Settings, DEFAULT_START

APP_NAME = "منزّل صور منتجات ساك"
VERSION = "2.0"
CFG = Path(os.environ.get("APPDATA", Path.home())) / "SAAK_Downloader" / "settings.json"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")


def open_path(p):
    p = str(p)
    if sys.platform.startswith("win"):
        os.startfile(p)  # noqa
    elif sys.platform == "darwin":
        os.system(f'open "{p}"')
    else:
        os.system(f'xdg-open "{p}"')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{VERSION}")
        self.geometry("860x640")
        self.minsize(760, 560)
        self.option_add("*Font", FONT)
        try:
            self.iconbitmap(default=self._res("app.ico"))
        except Exception:
            pass
        self.q: queue.Queue = queue.Queue()
        self.crawler: Crawler | None = None
        self.worker: threading.Thread | None = None
        self.vars = {
            "url": tk.StringVar(value=DEFAULT_START),
            "out": tk.StringVar(value=str(Path.home() / "Downloads" / "SAAK_Product_Images")),
            "filter": tk.StringVar(value="/منتجاتنا"),
            "pages": tk.IntVar(value=500),
            "depth": tk.IntVar(value=5),
            "minpx": tk.IntVar(value=180),
            "minkb": tk.IntVar(value=5),
            "delay": tk.DoubleVar(value=0.15),
            "skip": tk.StringVar(value=""),
            "robots": tk.BooleanVar(value=True),
            "zip": tk.BooleanVar(value=True),
        }
        self._load_cfg()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._pump)

    @staticmethod
    def _res(name):
        base = getattr(sys, "_MEIPASS", Path(__file__).parent)
        return str(Path(base) / name)

    # ------------------------------------------------------------- layout
    def _build(self):
        st = ttk.Style(self)
        try:
            st.theme_use("vista" if sys.platform.startswith("win") else "clam")
        except Exception:
            pass
        st.configure("Accent.TButton", font=FONT_B)

        hdr = tk.Frame(self, bg="#123b5c")
        hdr.pack(fill="x")
        tk.Label(hdr, text=APP_NAME, bg="#123b5c", fg="white",
                 font=("Segoe UI", 15, "bold")).pack(side="right", padx=14, pady=8)
        tk.Label(hdr, text="تنزيل صور المنتجات الأصلية + فهرس CSV + أرشيف ZIP",
                 bg="#123b5c", fg="#cfe3f2").pack(side="right", padx=4)

        frm = ttk.LabelFrame(self, text="الإعدادات")
        frm.pack(fill="x", padx=10, pady=8)
        frm.columnconfigure(1, weight=1)

        def row(r, label, widget, extra=None):
            ttk.Label(frm, text=label).grid(row=r, column=2, sticky="e", padx=6, pady=3)
            widget.grid(row=r, column=1, sticky="ew", padx=4, pady=3)
            if extra:
                extra.grid(row=r, column=0, padx=6, pady=3)

        row(0, "رابط البداية:", ttk.Entry(frm, textvariable=self.vars["url"]))
        row(1, "مجلد الحفظ:", ttk.Entry(frm, textvariable=self.vars["out"]),
            ttk.Button(frm, text="استعراض…", command=self._browse))
        row(2, "مرشح المسار:", ttk.Entry(frm, textvariable=self.vars["filter"]))
        row(3, "كلمات استبعاد إضافية (مفصولة بفاصلة):", ttk.Entry(frm, textvariable=self.vars["skip"]))

        nums = ttk.Frame(frm)
        nums.grid(row=4, column=0, columnspan=3, sticky="e", pady=4)
        spec = [("أقصى صفحات", "pages", 1, 5000, 1), ("العمق", "depth", 0, 15, 1),
                ("أدنى أبعاد (px)", "minpx", 0, 4000, 10), ("أدنى حجم (KB)", "minkb", 0, 5000, 1),
                ("مهلة بين الطلبات (ث)", "delay", 0, 10, 0.05)]
        for lbl, key, lo, hi, inc in spec:
            ttk.Label(nums, text=lbl).pack(side="right", padx=(10, 2))
            ttk.Spinbox(nums, from_=lo, to=hi, increment=inc, width=7,
                        textvariable=self.vars[key], justify="center").pack(side="right")

        chk = ttk.Frame(frm)
        chk.grid(row=5, column=0, columnspan=3, sticky="e")
        ttk.Checkbutton(chk, text="احترام robots.txt", variable=self.vars["robots"]).pack(side="right", padx=8)
        ttk.Checkbutton(chk, text="إنشاء أرشيف ZIP", variable=self.vars["zip"]).pack(side="right", padx=8)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10)
        self.btn_start = ttk.Button(bar, text="▶  بدء التنزيل", style="Accent.TButton", command=self._start)
        self.btn_start.pack(side="right", padx=4)
        self.btn_stop = ttk.Button(bar, text="■  إيقاف", command=self._stop, state="disabled")
        self.btn_stop.pack(side="right", padx=4)
        ttk.Button(bar, text="فتح المجلد", command=lambda: self._open("dir")).pack(side="left", padx=4)
        ttk.Button(bar, text="فتح الفهرس CSV", command=lambda: self._open("csv")).pack(side="left", padx=4)

        stats = ttk.Frame(self)
        stats.pack(fill="x", padx=10, pady=6)
        self.lbl = {}
        for key, t in (("pages", "صفحات"), ("images", "صور جديدة"), ("skipped", "مستبعدة"), ("failed", "فشل")):
            f = ttk.Frame(stats, relief="groove", padding=4)
            f.pack(side="right", padx=4, expand=True, fill="x")
            ttk.Label(f, text=t).pack()
            self.lbl[key] = ttk.Label(f, text="0", font=("Segoe UI", 14, "bold"))
            self.lbl[key].pack()
        self.pb = ttk.Progressbar(self, mode="indeterminate")
        self.pb.pack(fill="x", padx=10)

        logf = ttk.LabelFrame(self, text="السجل")
        logf.pack(fill="both", expand=True, padx=10, pady=8)
        self.txt = tk.Text(logf, wrap="word", height=12, font=("Consolas", 9), bg="#0f1720", fg="#d7e3ee")
        sb = ttk.Scrollbar(logf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="left", fill="y")
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("rtl", justify="right")

        self.status = tk.StringVar(value="جاهز")
        ttk.Label(self, textvariable=self.status, anchor="e", relief="sunken").pack(fill="x", side="bottom")

    # ------------------------------------------------------------ actions
    def _browse(self):
        d = filedialog.askdirectory(initialdir=str(Path(self.vars["out"].get()).parent))
        if d:
            self.vars["out"].set(str(Path(d) / "SAAK_Product_Images"))

    def _open(self, what):
        out = Path(self.vars["out"].get())
        target = out if what == "dir" else out / "image_index.csv"
        if not target.exists():
            messagebox.showinfo(APP_NAME, "لم يُنشأ بعد. ابدأ التنزيل أولًا.")
            return
        open_path(target)

    def _settings(self) -> Settings:
        v = self.vars
        return Settings(start_url=v["url"].get().strip(), out_dir=Path(v["out"].get().strip()),
                        path_filter=v["filter"].get().strip(), max_pages=int(v["pages"].get()),
                        max_depth=int(v["depth"].get()), min_px=int(v["minpx"].get()),
                        min_bytes=int(v["minkb"].get()) * 1000, delay=float(v["delay"].get()),
                        respect_robots=v["robots"].get(), make_zip=v["zip"].get(),
                        extra_skip=[w.strip() for w in v["skip"].get().split(",") if w.strip()])

    def _start(self):
        try:
            s = self._settings()
        except (tk.TclError, ValueError):
            messagebox.showerror(APP_NAME, "قيمة رقمية غير صالحة في الإعدادات.")
            return
        if not s.start_url.startswith(("http://", "https://")):
            messagebox.showerror(APP_NAME, "رابط البداية غير صالح.")
            return
        self._save_cfg()
        self.txt.delete("1.0", "end")
        for k in self.lbl:
            self.lbl[k].config(text="0")
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.pb.start(12)
        self.status.set("جارٍ التنزيل…")
        self.crawler = Crawler(s, log=lambda m: self.q.put(("log", m)),
                               progress=lambda st: self.q.put(("stats", vars(st).copy())))

        def work():
            try:
                res = self.crawler.run()
                self.q.put(("done", res))
            except Exception as e:
                self.q.put(("error", str(e)))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        if self.crawler:
            self.crawler.stop()
            self.status.set("جارٍ الإيقاف…")
            self.btn_stop.config(state="disabled")

    def _pump(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.txt.insert("end", data + "\n", "rtl")
                    self.txt.see("end")
                elif kind == "stats":
                    for k, val in data.items():
                        if k in self.lbl:
                            self.lbl[k].config(text=str(val))
                elif kind in ("done", "error"):
                    self.pb.stop()
                    self.btn_start.config(state="normal")
                    self.btn_stop.config(state="disabled")
                    if kind == "done":
                        self.status.set(f"{'أُوقف' if data['stopped'] else 'اكتمل'} — "
                                        f"{data['records']} صورة في الفهرس")
                        if data["records"] and messagebox.askyesno(APP_NAME, "انتهى التنزيل. فتح المجلد؟"):
                            open_path(self.vars["out"].get())
                    else:
                        self.status.set("خطأ")
                        messagebox.showerror(APP_NAME, data)
        except queue.Empty:
            pass
        self.after(120, self._pump)

    # ------------------------------------------------------------ config
    def _load_cfg(self):
        try:
            d = json.loads(CFG.read_text(encoding="utf-8"))
            for k, v in d.items():
                if k in self.vars:
                    self.vars[k].set(v)
        except Exception:
            pass

    def _save_cfg(self):
        try:
            CFG.parent.mkdir(parents=True, exist_ok=True)
            CFG.write_text(json.dumps({k: v.get() for k, v in self.vars.items()},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "التنزيل جارٍ. إيقاف والخروج؟"):
                return
            self.crawler.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
