"""İnovasyon motoru: RAG (ChromaDB) + Web (DuckDuckGo) + yerel LLM (Ollama, LangChain).

Akış (generate_idea):
    1. ChromaDB'de kategoriyle ilgili geçmiş projeler taranır   -> rag_context  (Context 1)
    2. DuckDuckGo'da kategoriyle ilgili güncel gelişmeler aranır -> web_context  (Context 2)
    3. Her iki bağlam sistem promptuna gömülüp Ollama'ya gönderilir, cevap akış (stream) olarak döner.

Bağımsız test:
    python ai_engine.py Lise "Afet Yönetimi"
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Callable, Iterator

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

import config

# ---------------------------------------------------------------------- #
# Sistem promptu
# ---------------------------------------------------------------------- #
SYSTEM_PROMPT = """Sen üst düzey bir İnovasyon Mühendisi ve Teknofest/TÜBİTAK Proje Danışmanısın. Amacın, {seviye} seviyesindeki öğrenciler için {kategori} alanında, daha önce ASLA yapılmamış, özgün ve prototiplenebilir bir proje fikri üretmektir.
Aşağıda sana geçmişte yapılan projelerin verisi (Geçmiş Veri) ve internetteki en güncel trendler (Güncel Veri) verilmiştir.
Görevlerin:
1. Geçmiş veriyi analiz et ve klasikleşmiş, yüzlerce kez yapılmış fikirleri doğrudan ele.
2. Güncel veriyi kullanarak, klasik bir probleme nesnelerin interneti (IoT), biyotaklit (biyomimikri), derin öğrenme veya yeni nesil materyaller ekleyerek inovatif bir çözüm yarat.
3. Proje fikri seçilen eğitim seviyesinin teknik kapasitesine uygun olmalı (Örn: İlkokul için kimyasal/basit, Üniversite için karmaşık algoritmik).
Çıktı Formatı:
- Proje Adı (Çarpıcı ve akılda kalıcı)
- Klasik Çözüm Neden Yetersiz?
- İnovasyon Önerisi (Bizim projemiz neyi farklı yapıyor?)
- Kullanılacak Teknolojiler / Malzemeler
- Prototiplenme Adımları

Geçmiş Veri: {rag_context}
Güncel Veri: {web_context}

Ek kurallar:
- Yanıtın tamamını Türkçe yaz ve yukarıdaki beş başlığı aynen, bu sırayla kullan.
- Seviye profili ({seviye}): {seviye_profili}
- Malzemeler gerçekçi, bulunabilir ve yaklaşık bütçesi seviyeye uygun olsun; prototip adımları numaralı ve uygulanabilir olsun.
- Güncel Veri'deki hangi gelişmeden ilham aldığını İnovasyon Önerisi içinde kısaca belirt.
- Geçmiş Veri'deki bir projeye benzeyen bir fikir üretme; benzerlik riski varsa farkı açıkça vurgula."""

HUMAN_PROMPT = "{seviye} seviyesi ve {kategori} kategorisi için özgün proje fikrini şimdi üret."

SEVIYE_PROFILLERI = {
    "İlkokul": "8-10 yaş; güvenli, ev/okulda bulunabilen malzemeler, basit kimyasal/fiziksel deneyler, "
               "gözleme dayalı ölçüm. Lehim, şebeke elektriği, tehlikeli kimyasal ve kodlama gerektirmesin.",
    "Ortaokul": "11-14 yaş; Arduino/micro:bit gibi blok veya basit kodlamalı kartlar, hazır sensör modülleri, "
                "3B yazıcı ve basit mekanik düzenekler kullanılabilir.",
    "Lise": "15-18 yaş; Arduino/ESP32/Raspberry Pi, Python ile veri analizi, hazır (önceden eğitilmiş) "
            "yapay zeka modellerinin uyarlanması, basit mobil/web uygulaması, laboratuvar ölçümleri.",
    "Üniversite": "Lisans/lisansüstü; özgün algoritma tasarımı, derin öğrenme modelinin sıfırdan eğitimi, "
                  "gömülü sistemler, PCB tasarımı, malzeme sentezi, simülasyon ve ölçeklenebilir mimari.",
}

NO_RAG_TEXT = (
    "Veritabanında bu kategoriye ait geçmiş proje bulunamadı. Bu durumda Teknofest/TÜBİTAK "
    "yarışmalarında yaygın olarak yapılmış klasik projeleri (ör. akıllı çöp kutusu, otomatik sulama, "
    "akıllı baston, gaz kaçağı alarmı, deprem çantası uygulaması vb.) kendi bilginden hatırla ve ele."
)
NO_WEB_TEXT = (
    "Web araması kapalı veya sonuç alınamadı. Kendi bilgindeki en güncel teknolojik gelişmeleri "
    "(IoT, biyomimikri, derin öğrenme, yeni nesil materyaller) kullan."
)

StatusCallback = Callable[[str], None]


class InnovationEngine:
    """GUI'den bağımsız çalışan motor. Thread-güvenli tembel (lazy) başlatma kullanır."""

    def __init__(self, model: str = config.LLM_MODEL) -> None:
        self.model = model
        self._llm = None
        self._store = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Bileşenler
    # ------------------------------------------------------------------ #
    @property
    def llm(self):
        with self._lock:
            if self._llm is None:
                from langchain_ollama import ChatOllama

                self._llm = ChatOllama(
                    model=self.model,
                    base_url=config.OLLAMA_BASE_URL,
                    temperature=config.LLM_TEMPERATURE,
                    num_ctx=config.LLM_NUM_CTX,
                )
            return self._llm

    def _get_store(self):
        with self._lock:
            if self._store is None:
                if not config.CHROMA_DIR.exists():
                    return None
                from langchain_chroma import Chroma
                from langchain_ollama import OllamaEmbeddings

                self._store = Chroma(
                    collection_name=config.CHROMA_COLLECTION,
                    embedding_function=OllamaEmbeddings(
                        model=config.EMBED_MODEL, base_url=config.OLLAMA_BASE_URL,
                    ),
                    persist_directory=str(config.CHROMA_DIR),
                    client_settings=config.chroma_client_settings(),
                )
            return self._store

    # ------------------------------------------------------------------ #
    # Sağlık kontrolleri
    # ------------------------------------------------------------------ #
    def required_models(self) -> list[str]:
        return [self.model, config.EMBED_MODEL]

    def missing_models(self) -> list[str] | None:
        """İndirilmemiş modelleri döner; Ollama'ya ulaşılamazsa None döner."""
        try:
            with urllib.request.urlopen(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, OSError, ValueError):
            return None

        names = {m.get("name", "") for m in data.get("models", [])}
        bases = {n.split(":")[0] for n in names}
        return [m for m in self.required_models() if m not in names and m not in bases]

    def check_ollama(self) -> tuple[bool, str]:
        """Ollama sunucusunun ayakta ve modelin indirilmiş olduğunu (yerelde) kontrol eder."""
        missing = self.missing_models()
        if missing is None:
            return False, ("Ollama çalışmıyor. Ollama uygulamasını başlatın "
                           "(Başlat menüsü → Ollama) veya 'ollama serve' çalıştırın.")
        if self.model in missing:
            return False, f"Model eksik: {self.model} ('Modelleri İndir' butonunu kullanın)."
        msg = f"Ollama hazır • {self.model}"
        if config.EMBED_MODEL in missing:
            msg += f" (RAG için {config.EMBED_MODEL} eksik)"
        return True, msg

    @staticmethod
    def pull_model(name: str, on_progress: Callable[[str, float | None], None] | None = None) -> None:
        """Modeli yerel Ollama API'si üzerinden indirir (terminalde 'ollama pull' ile aynı iş).

        on_progress(durum_metni, 0..1 arası oran veya None) ile ilerleme bildirilir.
        """
        progress = on_progress or (lambda _s, _f: None)
        req = urllib.request.Request(
            f"{config.OLLAMA_BASE_URL}/api/pull",
            data=json.dumps({"model": name, "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if "error" in event:
                    raise RuntimeError(f"{name} indirilemedi: {event['error']}")
                total, done = event.get("total"), event.get("completed")
                frac = done / total if total and done is not None else None
                progress(event.get("status", ""), frac)
                if event.get("status") == "success":
                    return
        raise RuntimeError(f"{name} indirme işlemi yarıda kesildi.")

    def past_project_count(self) -> int:
        try:
            store = self._get_store()
            return len(store.get(include=[])["ids"]) if store else 0
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------------ #
    # Context 1: Geçmiş projeler (RAG)
    # ------------------------------------------------------------------ #
    def retrieve_past_projects(self, seviye: str, kategori: str) -> tuple[str, list[str]]:
        store = self._get_store()
        if store is None or not store.get(limit=1, include=[])["ids"]:
            return "", []

        query = f"{kategori} alanında {seviye} öğrencilerinin yaptığı proje: problem, çözüm, yöntem, malzemeler"
        k = config.RAG_TOP_K
        docs = []
        # Önce kategori etiketiyle filtrele (alt klasör adı), yoksa tüm koleksiyonda ara.
        # MMR: birbirinin kopyası parçalar yerine farklı projelerden çeşitli örnekler getirir.
        try:
            docs = store.max_marginal_relevance_search(query, k=k, fetch_k=k * 4,
                                                       filter={"kategori": kategori})
        except Exception:  # noqa: BLE001 - filtre/MMR desteklenmezse sade aramaya düş
            docs = []
        if not docs:
            docs = store.max_marginal_relevance_search(query, k=k, fetch_k=k * 4)

        parts: list[str] = []
        sources: list[str] = []
        total = 0
        for d in docs:
            title = d.metadata.get("baslik") or d.metadata.get("source", "Bilinmeyen proje")
            block = f"[Geçmiş Proje: {title} | {d.metadata.get('kategori', 'Genel')}]\n{d.page_content.strip()}"
            if total + len(block) > config.RAG_MAX_CHARS:
                break
            parts.append(block)
            total += len(block)
            if title not in sources:
                sources.append(title)
        return "\n\n".join(parts), sources

    # ------------------------------------------------------------------ #
    # Context 2: Güncel trendler (Web)
    # ------------------------------------------------------------------ #
    @staticmethod
    def search_web(kategori: str) -> tuple[str, list[str]]:
        from search_module import search_trends

        summary, items = search_trends(kategori)
        return summary, [f"{it.title} — {it.url}" for it in items]

    # ------------------------------------------------------------------ #
    # Ana akış
    # ------------------------------------------------------------------ #
    def build_chain(self):
        prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)])
        return prompt | self.llm | StrOutputParser()

    def generate_idea(
        self,
        seviye: str,
        kategori: str,
        use_web: bool = True,
        on_status: StatusCallback | None = None,
    ) -> Iterator[str]:
        """Proje fikrini parça parça (stream) üretir. Son parça kaynak listesidir."""
        status = on_status or (lambda _msg: None)
        notes: list[str] = []

        # 1) Geçmiş projeler
        status("Geçmiş projeler taranıyor (ChromaDB)")
        try:
            rag_context, rag_sources = self.retrieve_past_projects(seviye, kategori)
        except Exception as exc:  # noqa: BLE001
            rag_context, rag_sources = "", []
            notes.append(f"Geçmiş proje veritabanı okunamadı: {friendly_error(exc)}")

        # 2) Güncel trendler
        web_context, web_sources = "", []
        if use_web:
            status("İnternetteki güncel trendler aranıyor (DuckDuckGo)")
            try:
                web_context, web_sources = self.search_web(kategori)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Web araması yapılamadı: {exc}")

        # 3) LLM
        status(f"Ollama ({self.model}) fikir üretiyor")
        variables = {
            "seviye": seviye,
            "kategori": kategori,
            "seviye_profili": SEVIYE_PROFILLERI.get(seviye, ""),
            "rag_context": rag_context or NO_RAG_TEXT,
            "web_context": web_context or NO_WEB_TEXT,
        }
        try:
            for chunk in self.build_chain().stream(variables):
                yield chunk
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(friendly_error(exc)) from exc

        yield _format_sources(rag_sources, web_sources, notes)


def _format_sources(rag_sources: list[str], web_sources: list[str], notes: list[str]) -> str:
    lines = ["", "", "─" * 60, "Kaynaklar (bu fikir üretilirken kullanılan bağlam)"]
    lines.append("• Geçmiş projeler: " + (", ".join(rag_sources) if rag_sources
                                         else "veritabanında eşleşme yok (genel bilgi kullanıldı)"))
    if web_sources:
        lines.append("• Güncel web kaynakları:")
        lines.extend(f"    - {s}" for s in web_sources)
    else:
        lines.append("• Güncel web kaynakları: kullanılmadı")
    lines.extend(f"⚠ {n}" for n in notes)
    return "\n".join(lines) + "\n"


def friendly_error(exc: Exception) -> str:
    """Teknik hata mesajlarını kullanıcının anlayacağı Türkçe açıklamalara çevirir."""
    msg = str(exc)
    low = msg.lower()
    if "connect" in low or "refused" in low or "connection" in low:
        return ("Ollama'ya bağlanılamadı. Ollama uygulamasını başlatın "
                "(Başlat menüsü → Ollama) veya 'ollama serve' çalıştırın.")
    if "not found" in low and "model" in low:
        return (f"Model bulunamadı. 'Modelleri İndir' butonunu kullanın ya da "
                f"'ollama pull {config.LLM_MODEL}' ve 'ollama pull {config.EMBED_MODEL}' çalıştırın.")
    return msg


if __name__ == "__main__":
    import sys

    lvl = sys.argv[1] if len(sys.argv) > 1 else "Lise"
    cat = sys.argv[2] if len(sys.argv) > 2 else "Çevre"
    engine = InnovationEngine()
    print(engine.check_ollama()[1])
    for part in engine.generate_idea(lvl, cat, on_status=lambda m: print(f"[{m}]")):
        print(part, end="", flush=True)
