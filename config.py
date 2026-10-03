"""Tüm modüllerin paylaştığı ayarlar.

database_builder.py ile ai_engine.py aynı embedding modelini ve aynı ChromaDB
klasörünü kullanmak zorundadır; bu yüzden değerler tek bir yerde tutulur.
Ortam değişkenleriyle (ör. INOVASYON_LLM_MODEL=gemma2) kod değiştirmeden ezilebilir.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- Gizlilik: hiçbir kütüphane dışarıya telemetri/iz göndermesin ---
os.environ["ANONYMIZED_TELEMETRY"] = "False"   # ChromaDB (PostHog) telemetrisi
os.environ["LANGCHAIN_TRACING_V2"] = "false"   # LangSmith izleme
os.environ["LANGSMITH_TRACING"] = "false"

# --- Ollama ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
LLM_MODEL = os.getenv("INOVASYON_LLM_MODEL", "llama3.1")          # veya "gemma2"
EMBED_MODEL = os.getenv("INOVASYON_EMBED_MODEL", "nomic-embed-text")
LLM_TEMPERATURE = float(os.getenv("INOVASYON_TEMPERATURE", "0.8"))  # yaratıcılık için biraz yüksek
LLM_NUM_CTX = int(os.getenv("INOVASYON_NUM_CTX", "8192"))

# --- ChromaDB ---
PROJECTS_DIR = BASE_DIR / "gecmis_projeler"
CHROMA_DIR = BASE_DIR / "chroma_db"
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
