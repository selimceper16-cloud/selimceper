"""Tüm modüllerin paylaştığı ayarlar.

database_builder.py ile ai_engine.py aynı embedding modelini ve aynı ChromaDB
klasörünü kullanmak zorundadır; bu yüzden değerler tek bir yerde tutulur.
Ortam değişkenleriyle (ör. INOVASYON_LLM_MODEL=gemma2) kod değiştirmeden ezilebilir.
"""

import os
import sys
from pathlib import Path

APP_NAME = "InovasyonZekasi"
APP_TITLE = "TÜBİTAK / Teknofest İnovasyon Zekası"
APP_VERSION = "1.1.0"

# PyInstaller ile paketlenmiş (kurulu .exe) mi, kaynak koddan mı çalışıyoruz?
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(__file__).resolve().parent
# İkon gibi pakete gömülü dosyaların yeri
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))

if FROZEN:
    # Kurulu uygulama "Program Files" altındadır ve oraya yazamaz; kullanıcı verisi
    # kullanıcının kendi klasörlerinde tutulur.
    _local = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    DATA_DIR = _local / APP_NAME                                  # veritabanı (gizli klasör)
    USER_DIR = Path.home() / "Documents" / APP_NAME               # kullanıcının göreceği klasör
else:
    DATA_DIR = BASE_DIR
    USER_DIR = BASE_DIR

# --- Gizlilik: hiçbir kütüphane dışarıya telemetri/iz göndermesin ---
os.environ["ANONYMIZED_TELEMETRY"] = "False"   # ChromaDB (PostHog) telemetrisi
os.environ["LANGCHAIN_TRACING_V2"] = "false"   # LangSmith izleme
os.environ["LANGSMITH_TRACING"] = "false"

# --- Ollama ---
# Not: Ollama'nın kendi OLLAMA_HOST değişkeni sunucu adresi içindir ve şema içermeyebilir;
# karışmasın diye ayrı bir değişken kullanılır.
OLLAMA_BASE_URL = os.getenv("INOVASYON_OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("INOVASYON_LLM_MODEL", "llama3.1")          # veya "gemma2"
EMBED_MODEL = os.getenv("INOVASYON_EMBED_MODEL", "nomic-embed-text")
LLM_TEMPERATURE = float(os.getenv("INOVASYON_TEMPERATURE", "0.8"))  # yaratıcılık için biraz yüksek
LLM_NUM_CTX = int(os.getenv("INOVASYON_NUM_CTX", "8192"))

# --- ChromaDB ---
PROJECTS_DIR = Path(os.getenv("INOVASYON_PROJE_KLASORU", USER_DIR / "gecmis_projeler"))
CHROMA_DIR = Path(os.getenv("INOVASYON_DB_KLASORU", DATA_DIR / "chroma_db"))
CHROMA_COLLECTION = "gecmis_projeler"


def chroma_client_settings():
    """ChromaDB'yi telemetrisi kapalı, tamamen yerel modda açmak için ayarlar."""
    from chromadb.config import Settings

    return Settings(anonymized_telemetry=False, is_persistent=True,
                    persist_directory=str(CHROMA_DIR))


# --- Parçalama (text splitter) ---
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# --- RAG / Web bağlam limitleri (LLM bağlam penceresini taşırmamak için) ---
RAG_TOP_K = 6
RAG_MAX_CHARS = 5000
WEB_MAX_RESULTS = 8
WEB_MAX_CHARS = 3500

EGITIM_SEVIYELERI = ["İlkokul", "Ortaokul", "Lise", "Üniversite"]

# Kategori -> web aramasında kullanılacak İngilizce anahtar kelimeler
# (güncel teknoloji haberleri İngilizce kaynaklarda çok daha zengin).
KATEGORILER = {
    "Çevre": "environment sustainability climate technology",
    "Sağlık": "healthcare medical technology",
    "Tarım": "agriculture agritech smart farming",
    "Afet Yönetimi": "disaster management early warning earthquake technology",
    "Yapay Zeka": "artificial intelligence machine learning",
    "Enerji": "renewable energy storage technology",
    "Ulaşım": "smart mobility transportation technology",
    "Eğitim Teknolojileri": "education technology edtech",
    "Su Teknolojileri": "water purification water management technology",
    "Biyoteknoloji": "biotechnology synthetic biology",
    "Robotik": "robotics innovation",
    "Malzeme Bilimi": "new materials nanomaterials innovation",
}
