"""TÜBİTAK / Teknofest İnovasyon Zekası - Masaüstü arayüzü (CustomTkinter).

Çalıştırma:
    python main.py
    python main.py --selftest   # paketlenmiş exe'nin bağımlılıklarını doğrular (CI için)

Ağır işler (ChromaDB sorgusu, web araması, LLM üretimi, model indirme, veritabanı
güncelleme) arka plan thread'inde yürütülür; arayüz bir kuyruk (queue) üzerinden
periyodik olarak güncellenir. Böylece pencere hiçbir zaman donmaz.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from tkinter import filedialog, messagebox
from typing import Callable

import config

# Konsolsuz (windowed) exe'de stdout/stderr yoktur; kütüphaneler yazmaya çalışınca
# çökmesin diye çıktılar kullanıcı veri klasöründeki bir kayıt dosyasına yönlendirilir.
if sys.stdout is None or sys.stderr is None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _log = open(config.DATA_DIR / "uygulama_kaydi.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

import customtkinter as ctk  # noqa: E402

from ai_engine import InnovationEngine, friendly_error  # noqa: E402
from config import EGITIM_SEVIYELERI, KATEGORILER, LLM_MODEL  # noqa: E402

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# --- Renk paleti ---
BG_SIDEBAR = "#14161c"
BG_MAIN = "#1b1e26"
BG_TEXT = "#11131a"
BG_BUTTON = "#232733"
BG_BUTTON_HOVER = "#2c3140"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
FG_MUTED = "#8b93a7"
FG_HEADING = "#7dd3fc"
OK_GREEN = "#22c55e"
WARN_YELLOW = "#eab308"
ERR_RED = "#ef4444"

SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
BACKEND_RECHECK_MS = 8000

WELCOME_TEXT = f"""Hoş geldin! 👋

1. Sol panelden eğitim seviyesini ve yarışma kategorisini seç.
2. "Fikir Üret" butonuna bas.

Arka planda şunlar olur:
  • Yerel veritabanındaki geçmiş proje raporları taranır (klasik fikirleri elemek için),
  • İstersen DuckDuckGo üzerinden son 1 yılın teknoloji trendleri çekilir,
  • Bilgisayarındaki Ollama modeli bu iki bağlamı harmanlayıp özgün bir proje fikri yazar.

Geçmiş projeleri eklemek için:
  1. "📁 Proje Klasörünü Aç" butonuna bas (açılan klasör: {config.PROJECTS_DIR}),
  2. PDF / TXT / DOCX raporlarını içine kopyala
     (kategori adında alt klasör açarsan, ör. "Tarım", arama o kategoriye odaklanır),
  3. "🔄 Veritabanını Güncelle" butonuna bas.
"""

FOLDER_README = """Geçmiş TÜBİTAK / Teknofest proje raporlarını (PDF, TXT, MD, DOCX) bu klasöre koyun.

Kategoriye göre alt klasör açarsanız (klasör adı uygulamadaki kategoriyle aynı olmalı),
arama önce o kategorideki projelere odaklanır:

    Tarım\\akilli_sera.pdf
    Sağlık\\proje_raporu.docx
    genel_proje.txt      -> "Genel" kategorisi

Ardından uygulamada "Veritabanını Güncelle" butonuna basın.
Not: Taranmış (resim) PDF'lerde metin olmadığı için atlanırlar; önce OCR uygulayın.
"""


class InnovationApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{config.APP_TITLE}  v{config.APP_VERSION}")
        self.geometry("1280x820")
        self.minsize(980, 680)
        self.configure(fg_color=BG_MAIN)
        self._set_icon()

        self.engine = InnovationEngine()
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.is_busy = False
        self.spinner_index = 0
        self.status_base = ""
        self.missing_models: list[str] = []
        self.backend_ok = False
        self.backend_check_running = False
        self.recheck_scheduled = False

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_panel()
        self._set_output(WELCOME_TEXT)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_events)
        self._start_backend_check()

    # ------------------------------------------------------------------ #
    # Arayüz kurulumu
    # ------------------------------------------------------------------ #
    def _set_icon(self) -> None:
        icon = config.RESOURCE_DIR / "packaging" / "app.ico"
        if sys.platform == "win32" and icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except Exception:  # noqa: BLE001 - ikon kozmetiktir, hata uygulamayı durdurmasın
                pass

    def _small_button(self, parent, text: str, command: Callable) -> ctk.CTkButton:
        return ctk.CTkButton(parent, text=text, height=32, fg_color=BG_BUTTON,
                             hover_color=BG_BUTTON_HOVER, command=command)

    def _build_sidebar(self) -> None:
        sb = ctk.CTkFrame(self, width=310, corner_radius=0, fg_color=BG_SIDEBAR)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(11, weight=1)  # boşluk: alt kısımdaki öğeleri aşağı iter

        ctk.CTkLabel(
            sb, text="💡 İnovasyon Zekası",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, padx=24, pady=(28, 2), sticky="w")
        ctk.CTkLabel(
            sb, text="TÜBİTAK • Teknofest Proje Danışmanı\n%100 yerel yapay zeka",
            font=ctk.CTkFont(size=12), text_color=FG_MUTED, justify="left",
        ).grid(row=1, column=0, padx=24, pady=(0, 22), sticky="w")

        # Eğitim seviyesi
        ctk.CTkLabel(sb, text="Eğitim Seviyesi", font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=2, column=0, padx=24, pady=(0, 6), sticky="w")
        self.level_var = ctk.StringVar(value="Lise")
        self.level_menu = ctk.CTkOptionMenu(
            sb, values=EGITIM_SEVIYELERI, variable=self.level_var, height=36,
            fg_color=BG_BUTTON, button_color=BG_BUTTON_HOVER, button_hover_color="#363c4e",
            dropdown_fg_color=BG_BUTTON,
        )
        self.level_menu.grid(row=3, column=0, padx=24, pady=(0, 16), sticky="ew")

        # Kategori
        ctk.CTkLabel(sb, text="Yarışma Kategorisi", font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=4, column=0, padx=24, pady=(0, 6), sticky="w")
        self.category_var = ctk.StringVar(value="Çevre")
        self.category_menu = ctk.CTkOptionMenu(
            sb, values=list(KATEGORILER.keys()), variable=self.category_var, height=36,
            fg_color=BG_BUTTON, button_color=BG_BUTTON_HOVER, button_hover_color="#363c4e",
            dropdown_fg_color=BG_BUTTON,
        )
        self.category_menu.grid(row=5, column=0, padx=24, pady=(0, 16), sticky="ew")

        # Web araması anahtarı
        self.web_var = ctk.BooleanVar(value=True)
        self.web_switch = ctk.CTkSwitch(
            sb, text="İnternetten güncel trendleri tara", variable=self.web_var,
            font=ctk.CTkFont(size=12), progress_color=ACCENT,
        )
        self.web_switch.grid(row=6, column=0, padx=24, pady=(0, 20), sticky="w")

        # Fikir Üret butonu
        self.generate_btn = ctk.CTkButton(
            sb, text="🚀  Fikir Üret", height=46, corner_radius=10,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._on_generate,
        )
        self.generate_btn.grid(row=7, column=0, padx=24, pady=(0, 10), sticky="ew")

        # Yükleniyor göstergesi (başlangıçta gizli)
        self.progress = ctk.CTkProgressBar(sb, mode="indeterminate", height=6, progress_color=ACCENT)
        self.progress.grid(row=8, column=0, padx=24, pady=(4, 4), sticky="ew")
        self.progress.grid_remove()

        self.status_label = ctk.CTkLabel(
            sb, text="", font=ctk.CTkFont(size=12), text_color=FG_MUTED,
            justify="left", anchor="w", wraplength=260,
        )
        self.status_label.grid(row=9, column=0, padx=24, pady=(0, 6), sticky="ew")

        self.cancel_btn = ctk.CTkButton(
            sb, text="Durdur", height=30, fg_color="transparent", border_width=1,
            border_color="#3a3f4e", hover_color="#2a2e3a", command=self._on_cancel,
        )
        self.cancel_btn.grid(row=10, column=0, padx=24, pady=(0, 6), sticky="ew")
        self.cancel_btn.grid_remove()

        # Alt kısım: model indirme / veritabanı / kaydet / temizle / durum
        self.pull_btn = ctk.CTkButton(
            sb, text="⬇  Modelleri İndir", height=34, fg_color="#a16207",
            hover_color="#854d0e", command=self._on_pull_models,
        )
        self.pull_btn.grid(row=12, column=0, padx=24, pady=(0, 8), sticky="ew")
        self.pull_btn.grid_remove()

        tools = ctk.CTkFrame(sb, fg_color="transparent")
        tools.grid(row=13, column=0, padx=24, pady=(0, 8), sticky="ew")
        tools.grid_columnconfigure((0, 1), weight=1)
        self.folder_btn = self._small_button(tools, "📁  Proje Klasörünü Aç", self._on_open_folder)
        self.folder_btn.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.db_btn = self._small_button(tools, "🔄  Veritabanını Güncelle", self._on_update_db)
        self.db_btn.grid(row=1, column=0, columnspan=2, pady=(8, 0), sticky="ew")
        self.save_btn = self._small_button(tools, "💾 Kaydet", self._on_save)
        self.save_btn.grid(row=2, column=0, padx=(0, 5), pady=(8, 0), sticky="ew")
        self.clear_btn = self._small_button(tools, "🧹 Temizle", lambda: self._set_output(WELCOME_TEXT))
        self.clear_btn.grid(row=2, column=1, padx=(5, 0), pady=(8, 0), sticky="ew")

        self.backend_label = ctk.CTkLabel(
            sb, text=f"● Ollama kontrol ediliyor… ({LLM_MODEL})",
            font=ctk.CTkFont(size=11), text_color=FG_MUTED,
            justify="left", anchor="w", wraplength=260,
        )
        self.backend_label.grid(row=14, column=0, padx=24, pady=(4, 4), sticky="ew")
        self.db_label = ctk.CTkLabel(
            sb, text="● Veritabanı kontrol ediliyor…", font=ctk.CTkFont(size=11),
            text_color=FG_MUTED, justify="left", anchor="w", wraplength=260,
        )
        self.db_label.grid(row=15, column=0, padx=24, pady=(0, 18), sticky="ew")

    def _build_main_panel(self) -> None:
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_MAIN)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, padx=28, pady=(26, 10), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Üretilen Proje Fikri",
                     font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w")
        self.meta_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=12),
                                       text_color=FG_MUTED)
        self.meta_label.grid(row=0, column=1, sticky="e")

        # CTkTextbox kendi kaydırma çubuğuyla gelir (scrollable)
        self.output = ctk.CTkTextbox(
            main, wrap="word", corner_radius=12, fg_color=BG_TEXT,
            border_width=1, border_color="#262a36",
            font=ctk.CTkFont(size=15), spacing1=2, spacing3=4,
        )
        self.output.grid(row=1, column=0, padx=28, pady=(0, 28), sticky="nsew")
        self.output.tag_config("heading", foreground=FG_HEADING)
        self.output.tag_config("muted", foreground=FG_MUTED)

    # ------------------------------------------------------------------ #
    # Buton olayları
    # ------------------------------------------------------------------ #
    def _on_generate(self) -> None:
        if self.is_busy:
            return
        seviye = self.level_var.get()
        kategori = self.category_var.get()
        use_web = bool(self.web_var.get())
        self._set_output("")
        self.meta_label.configure(text=f"{seviye} • {kategori}")
        self._run_task(self._task_generate, seviye, kategori, use_web)

    def _on_pull_models(self) -> None:
        if self.is_busy:
            return
        models = self.missing_models or self.engine.required_models()
        self._set_output("")
        self.meta_label.configure(text="Model indirme")
        self._run_task(self._task_pull_models, models)

    def _on_update_db(self) -> None:
        if self.is_busy:
            return
        self._set_output("")
        self.meta_label.configure(text="Veritabanı güncelleme")
        self._run_task(self._task_update_db)

    def _on_open_folder(self) -> None:
        folder = ensure_projects_dir()
        try:
            if sys.platform == "win32":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            messagebox.showinfo("Proje Klasörü", f"Klasör: {folder}")

    def _on_cancel(self) -> None:
        if self.is_busy:
            self.cancel_event.set()
            self.status_base = "Durduruluyor"

    def _on_save(self) -> None:
        content = self.output.get("1.0", "end").strip()
        if not content or content == WELCOME_TEXT.strip():
            messagebox.showinfo("Kaydet", "Kaydedilecek bir proje fikri yok.")
            return
        default = f"proje_fikri_{datetime.now():%Y%m%d_%H%M}.md"
        path = filedialog.asksaveasfilename(
            defaultextension=".md", initialfile=default,
            filetypes=[("Markdown", "*.md"), ("Metin", "*.txt"), ("Tüm dosyalar", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            self.status_label.configure(text=f"Kaydedildi: {path}", text_color=OK_GREEN)

    def _on_close(self) -> None:
        self.cancel_event.set()
        self.destroy()

    # ------------------------------------------------------------------ #
    # Arka plan işleri (thread içinde çalışır; arayüze SADECE kuyrukla dokunur)
    # ------------------------------------------------------------------ #
    def _run_task(self, func: Callable, *args) -> None:
        self.cancel_event.clear()
        self._set_busy(True)

        def runner() -> None:
            try:
                func(*args)
            except Exception as exc:  # noqa: BLE001 - kullanıcıya her hatayı göstermek istiyoruz
                self.events.put(("error", friendly_error(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def _emit(self, text: str) -> None:
        self.events.put(("token", text))

    def _task_generate(self, seviye: str, kategori: str, use_web: bool) -> None:
        for chunk in self.engine.generate_idea(
            seviye, kategori, use_web=use_web,
            on_status=lambda msg: self.events.put(("status", msg)),
        ):
            if self.cancel_event.is_set():
                self._emit("\n\n[Üretim kullanıcı tarafından durduruldu.]")
                break
            self._emit(chunk)
        self.events.put(("done", ("✔ Fikir üretildi.", True)))

    def _task_pull_models(self, models: list[str]) -> None:
        for name in models:
            self._emit(f"⬇ {name} indiriliyor (ilk seferde birkaç dakika sürebilir)…\n")

            def progress(status: str, frac: float | None, name: str = name) -> None:
                if self.cancel_event.is_set():
                    raise RuntimeError("İndirme kullanıcı tarafından durduruldu.")
                pct = f" %{frac * 100:.0f}" if frac is not None else ""
                self.events.put(("status", f"{name}: {status}{pct}"))

            self.engine.pull_model(name, progress)
            self._emit(f"✔ {name} hazır.\n\n")
        self._check_backend()
        self.events.put(("done", ("✔ Modeller indirildi.", False)))

    def _task_update_db(self) -> None:
        import database_builder

        folder = ensure_projects_dir()
        self.events.put(("status", "Raporlar okunuyor ve veritabanına ekleniyor"))
        added = database_builder.build(folder, log=lambda line: self._emit(line + "\n"))
        self.events.put(("db", self.engine.past_project_count()))
        self.events.put(("done", (f"✔ Veritabanı güncellendi (+{added} parça).", False)))

    def _start_backend_check(self) -> None:
        if self.backend_check_running:
            return
        self.backend_check_running = True

        def runner() -> None:
            try:
                self._check_backend()
            finally:
                self.backend_check_running = False

        threading.Thread(target=runner, daemon=True).start()

    def _check_backend(self) -> None:
        missing = self.engine.missing_models()
        ok, msg = self.engine.check_ollama()
        self.events.put(("backend", (ok, msg, missing)))
        self.events.put(("db", self.engine.past_project_count()))

    # ------------------------------------------------------------------ #
    # Kuyruk -> arayüz
    # ------------------------------------------------------------------ #
    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "token":
                    if self.status_base.startswith("Ollama"):
                        self.status_base = "Fikir yazılıyor"
                    self.output.insert("end", payload)
                    self.output.see("end")
                elif kind == "status":
                    self.status_base = payload
                elif kind == "done":
                    message, highlight = payload
                    if highlight:
                        self._highlight_headings()
                    self._set_busy(False)
                    self.status_label.configure(text=message, text_color=OK_GREEN)
                elif kind == "error":
                    self._set_busy(False)
                    self.output.insert("end", f"\n\n⚠ Hata: {payload}\n")
                    self.output.see("end")
                    self.status_label.configure(text="⚠ Bir hata oluştu.", text_color=ERR_RED)
                elif kind == "backend":
                    self._show_backend(*payload)
                elif kind == "db":
                    self._show_db(payload)
        except queue.Empty:
            pass

        if self.is_busy:
            self._animate_status()
        self.after(50, self._poll_events)

    def _show_backend(self, ok: bool, msg: str, missing: list[str] | None) -> None:
        self.backend_ok = ok
        self.missing_models = missing or []
        color = (OK_GREEN if not self.missing_models else WARN_YELLOW) if ok else ERR_RED
        self.backend_label.configure(text=("● " if ok else "✖ ") + msg, text_color=color)
        if self.missing_models:
            n = len(self.missing_models)
            self.pull_btn.configure(text=f"⬇  Eksik Modelleri İndir ({n} model)")
            self.pull_btn.grid()
        else:
            self.pull_btn.grid_remove()
        # Ollama kapalıysa kullanıcı açana kadar periyodik olarak yeniden dene
        if missing is None and not self.recheck_scheduled:
            self.recheck_scheduled = True
            self.after(BACKEND_RECHECK_MS, self._recheck_backend)

    def _recheck_backend(self) -> None:
        self.recheck_scheduled = False
        self._start_backend_check()

    def _show_db(self, count: int) -> None:
        if count > 0:
            self.db_label.configure(text=f"● Geçmiş proje veritabanı: {count} parça",
                                    text_color=OK_GREEN)
        else:
            self.db_label.configure(text="● Geçmiş proje veritabanı boş: klasöre rapor ekleyip güncelleyin",
                                    text_color=WARN_YELLOW)

    # ------------------------------------------------------------------ #
    # Yardımcılar
    # ------------------------------------------------------------------ #
    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        state = "disabled" if busy else "normal"
        for widget in (self.level_menu, self.category_menu, self.web_switch, self.save_btn,
                       self.clear_btn, self.db_btn, self.pull_btn):
            widget.configure(state=state)
        if busy:
            self.status_base = "İşleniyor"
            self.generate_btn.configure(state="disabled", text="İşleniyor...")
            self.progress.grid()
            self.progress.start()
            self.cancel_btn.grid()
        else:
            self.generate_btn.configure(state="normal", text="🚀  Fikir Üret")
            self.progress.stop()
            self.progress.grid_remove()
            self.cancel_btn.grid_remove()

    def _animate_status(self) -> None:
        # Her ~100 ms'de bir spinner karesi ilerlet (poll 50 ms'de bir çalışıyor)
        self.spinner_index = (self.spinner_index + 1) % (len(SPINNER) * 2)
        frame = SPINNER[self.spinner_index // 2]
        dots = "." * (1 + (self.spinner_index // 6) % 3)
        self.status_label.configure(text=f"{frame}  {self.status_base}{dots}", text_color=FG_MUTED)
        self.generate_btn.configure(text=f"{frame}  İşleniyor...")

    def _set_output(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        if text:
            self.output.insert("1.0", text, "muted")
        if text == WELCOME_TEXT:
            self.meta_label.configure(text="")
            self.status_label.configure(text="")

    def _highlight_headings(self) -> None:
        """Başlık satırlarını (Markdown #, **...**, '- Proje Adı:' vb.) renklendirir."""
        keywords = ("proje adı", "klasik çözüm", "inovasyon önerisi",
                    "kullanılacak teknolojiler", "prototiplenme adımları", "kaynaklar")
        last_line = int(self.output.index("end-1c").split(".")[0])
        for i in range(1, last_line + 1):
            line = self.output.get(f"{i}.0", f"{i}.end")
            stripped = line.strip().lstrip("-•*# ").lower()
            if line.lstrip().startswith("#") or any(stripped.startswith(k) for k in keywords):
                self.output.tag_add("heading", f"{i}.0", f"{i}.end")


def ensure_projects_dir():
    """Proje klasörünü (yoksa) oluşturur ve içine kısa bir açıklama dosyası koyar."""
    folder = config.PROJECTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    readme = folder / "BENI_OKU.txt"
    if not readme.exists():
        readme.write_text(FOLDER_README, encoding="utf-8")
    return folder


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from selftest import run_selftest

        sys.exit(run_selftest())
    InnovationApp().mainloop()
