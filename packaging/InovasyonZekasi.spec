# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller yapılandırması.

Proje kök dizininden çalıştırın:
    pyinstaller --noconfirm --clean packaging/InovasyonZekasi.spec

Çıktı: dist/InovasyonZekasi/InovasyonZekasi.exe (+ _internal klasörü).
"onedir" modu kullanılır: tek dosyalı (onefile) exe her açılışta yüzlerce MB'ı geçici
klasöre açtığı için çok yavaş başlar; kurulum programı zaten tek bir Setup.exe üretir.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH PyInstaller tarafından tanımlanır

datas = [(str(ROOT / "packaging" / "app.ico"), "packaging")]
binaries = []
hiddenimports = []

# Veri dosyası / dinamik import / yerel (Rust) eklentisi olan paketler bütünüyle alınır
for pkg in ("customtkinter", "chromadb", "chromadb_rust_bindings", "ddgs", "primp"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# LangChain entegrasyonları ve ChromaDB'nin çalışma anında adla yüklediği modüller
for pkg in ("langchain_ollama", "langchain_chroma", "langchain_text_splitters",
            "langchain_core", "opentelemetry"):
    hiddenimports += collect_submodules(pkg)

# importlib.metadata ile sürüm / entry point okuyan paketlerin meta verileri
for dist in ("chromadb", "langchain-core", "langchain-ollama", "langchain-chroma",
             "opentelemetry-api", "opentelemetry-sdk", "ddgs"):
    try:
        datas += copy_metadata(dist)
    except Exception:  # noqa: BLE001 - paket yoksa (farklı sürüm) atla
        pass

# Uygulamanın hiç kullanmadığı ağır paketler (ChromaDB'nin varsayılan embedding'i yerine
# Ollama kullanılıyor) -> kurulum boyutunu ciddi şekilde küçültür.
excludes = [
    "onnxruntime", "torch", "tensorflow", "transformers", "sentence_transformers",
    "matplotlib", "pandas", "IPython", "jupyter", "notebook", "pytest",
    "kubernetes", "PyQt5", "PyQt6", "PySide2", "PySide6",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["selftest", "database_builder", "search_module"],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InovasyonZekasi",
    icon=str(ROOT / "packaging" / "app.ico"),
    console=False,          # siyah konsol penceresi açılmasın
    upx=False,              # UPX bazı antivirüslerde yanlış alarma yol açar
    version=str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name="InovasyonZekasi",
    upx=False,
)
