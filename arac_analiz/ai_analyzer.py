"""
Yapay zeka analiz modülü (AI Evaluator).

Her ilan için bir prompt oluşturur, Anthropic Claude API'sine gönderir ve yanıtı
Pydantic modeli (AracAnalizi) olarak doğrulanmış şekilde döndürür. API anahtarı
.env dosyasındaki ANTHROPIC_API_KEY değişkeninden okunur.

API anahtarı olmadan arayüzü denemek için demo_analiz() fonksiyonu, ağ çağrısı yapmadan
basit kurallarla örnek bir analiz üretir (gerçek bir yapay zeka değerlendirmesi değildir).
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from scraper import AracIlani, km_formatla, tl_formatla

logger = logging.getLogger(__name__)

# .env dosyasını bu modülün klasöründen (ve çalışma dizininden) yükle
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv()

VARSAYILAN_MODEL = "claude-opus-5-5"
VARSAYILAN_EFFORT = "medium"  # low | medium | high | xhigh | max
GECERLI_EFFORT = {"low", "medium", "high", "xhigh", "max"}


# ---------------------------------------------------------------------------
# Yanıt şeması
# ---------------------------------------------------------------------------

class AracAnalizi(BaseModel):
    alinir_mi: bool = Field(description="Bu fiyata, kilometreye ve hasar durumuna göre araç alınır mı?")
    genel_degerlendirme: str = Field(description="Araç ve ilan hakkında 2-4 cümlelik kısa özet ve kararın gerekçesi")
    artilari: list[str] = Field(description="Modelin/ilanın artıları, ör. 'Yakıt cimrisi', 'İkinci eli hızlı'")
    eksileri: list[str] = Field(description="Modelin/ilanın eksileri, ör. 'Yalıtım zayıf', 'Arka diz mesafesi dar'")
    kronik_sorunlar: list[str] = Field(description="Bu model/motor/şanzımanın bilinen kronik arızaları")
    parca_ve_bakim_maliyeti: str = Field(
        description="'Ucuz', 'Ortalama' veya 'Pahalı' ile başlayan kısa değerlendirme ve nedeni"
    )


class AnalizHatasi(Exception):
    """AI analizi yapılamadığında fırlatılır; mesaj kullanıcıya gösterilebilir."""


@dataclass
class AnalizSonucu:
    ilan: AracIlani
    analiz: AracAnalizi | None = None
    hata: str | None = None
    demo: bool = False


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SISTEM_PROMPTU = """Sen Türkiye ikinci el otomobil piyasasını çok iyi bilen, tarafsız bir oto ekspertiz \
ve satın alma danışmanısın. Kullanıcı sahibinden.com benzeri bir siteden bulduğu ilanı sana veriyor; \
amacı fiyat-performans açısından doğru aracı seçmek.

Değerlendirirken:
- Modelin (marka/seri/motor/şanzıman ve kuşak) Türkiye'deki genel itibarını, ikinci el likiditesini, \
yakıt tüketimini, konforunu ve yedek parça / servis maliyetlerini dikkate al.
- Fiyatı; yıl, kilometre, donanım ve hasar/tramer durumuyla birlikte genel piyasa bilginle kıyasla. \
Güncel piyasa verisine erişimin yok; fiyat yorumunu "piyasa ortalamasına göre makul / yüksek / düşük görünüyor" \
gibi temkinli ifadelerle yap.
- Ağır hasar kaydı, yüksek tramer, çok yüksek km veya açıklamadaki şüpheli ifadeler kararı olumsuz etkilemeli. \
Hasar bilgisi belirtilmemişse bunu eksi olarak not et ve ekspertiz öner.
- Kronik sorunlarda yalnızca bu model/motor/şanzıman için yaygın bilinen arızaları yaz; emin değilsen uydurma, \
listeyi kısa tut.
- Tüm metinleri Türkçe, kısa ve net yaz. Artı/eksi/kronik listelerinde 3-6 madde yeterli."""


def prompt_olustur(ilan: AracIlani) -> str:
    hasar = ilan.hasar
    aciklama = (ilan.aciklama or "").strip()
    if len(aciklama) > 3_000:
        aciklama = aciklama[:3_000] + " …"
    return f"""Aşağıdaki ilanı değerlendir.

<ilan>
Araç: {ilan.model_adi}
İlan başlığı: {ilan.baslik}
Fiyat: {tl_formatla(ilan.fiyat)}
Yıl: {ilan.yil or "Belirtilmemiş"}
Kilometre: {km_formatla(ilan.km)}
Kasa tipi: {ilan.kasa_tipi or "Belirtilmemiş"}
Hasar / Tramer durumu (açıklamadan otomatik çıkarıldı): {hasar.ozet}
İlan açıklaması:
{aciklama or "(açıklama yok)"}
</ilan>

Bu fiyata ve hasar durumuna göre araç alınır mı? Artılarını, eksilerini, kronik sorunlarını ve \
parça/bakım maliyetini değerlendir."""


# ---------------------------------------------------------------------------
# API çağrısı
# ---------------------------------------------------------------------------

def api_anahtari_var_mi() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def istemci_olustur() -> anthropic.Anthropic:
    anahtar = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not anahtar or anahtar.startswith("sk-ant-..."):
        raise AnalizHatasi(
            "ANTHROPIC_API_KEY bulunamadı. '.env.example' dosyasını '.env' olarak kopyalayıp anahtarınızı girin."
        )
    return anthropic.Anthropic(api_key=anahtar, max_retries=3, timeout=180.0)


def arac_analiz_et(
    ilan: AracIlani,
    istemci: anthropic.Anthropic | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> AracAnalizi:
    """Tek bir ilanı Claude ile analiz eder ve doğrulanmış AracAnalizi döndürür."""
    istemci = istemci or istemci_olustur()
    model = model or os.getenv("CLAUDE_MODEL", VARSAYILAN_MODEL)
    effort = effort or os.getenv("CLAUDE_EFFORT", VARSAYILAN_EFFORT)
    if effort not in GECERLI_EFFORT:
        effort = VARSAYILAN_EFFORT

    try:
        yanit = istemci.beta.messages.parse(
            model=model,
            max_tokens=16_000,
            system=SISTEM_PROMPTU,
            messages=[{"role": "user", "content": prompt_olustur(ilan)}],
            output_format=AracAnalizi,  # yanıt bu Pydantic şemasına uygun JSON olarak üretilir
            output_config={"effort": effort},
            # Güvenlik filtresi isteği reddederse API aynı isteği otomatik olarak uygun bir modelle tekrarlar
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise AnalizHatasi("API anahtarı geçersiz (401). .env dosyasındaki ANTHROPIC_API_KEY'i kontrol edin.") from e
    except anthropic.PermissionDeniedError as e:
        raise AnalizHatasi("API anahtarının bu modele erişim izni yok (403).") from e
    except anthropic.NotFoundError as e:
        raise AnalizHatasi(f"Model bulunamadı: {model}. CLAUDE_MODEL ayarını kontrol edin.") from e
    except anthropic.RateLimitError as e:
        raise AnalizHatasi("API hız sınırına ulaşıldı (429). Biraz bekleyip daha az ilanla tekrar deneyin.") from e
    except anthropic.BadRequestError as e:
        raise AnalizHatasi(f"Geçersiz istek (400): {e.message}") from e
    except anthropic.APIStatusError as e:
        raise AnalizHatasi(f"API hatası ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AnalizHatasi("Claude API'ye bağlanılamadı. İnternet bağlantınızı kontrol edin.") from e
    except ValidationError as e:
        raise AnalizHatasi("Model yanıtı beklenen JSON şemasına uymadı.") from e

    if yanit.stop_reason == "refusal":
        raise AnalizHatasi("Model bu ilanı değerlendirmeyi reddetti.")
    if yanit.stop_reason == "max_tokens":
        raise AnalizHatasi("Yanıt token sınırında kesildi; tekrar deneyin.")
    if yanit.parsed_output is None:
        raise AnalizHatasi("Modelden yapılandırılmış yanıt alınamadı.")
    return yanit.parsed_output


def toplu_analiz(
    ilanlar: list[AracIlani],
    max_paralel: int | None = None,
    ilerleme: Callable[[int, int, AnalizSonucu], None] | None = None,
    demo_modu: bool = False,
    effort: str | None = None,
) -> list[AnalizSonucu]:
    """
    İlanları paralel olarak analiz eder. Bir ilandaki hata diğerlerini durdurmaz; hata mesajı
    ilgili AnalizSonucu.hata alanına yazılır. Sonuçlar giriş sırasıyla döner.
    `ilerleme` geri çağrısı çağıran thread'de çalışır (Streamlit ilerleme çubuğu için güvenli).
    """
    if not ilanlar:
        return []
    if demo_modu:
        sonuclar = []
        for i, ilan in enumerate(ilanlar, 1):
            s = AnalizSonucu(ilan=ilan, analiz=demo_analiz(ilan), demo=True)
            sonuclar.append(s)
            if ilerleme:
                ilerleme(i, len(ilanlar), s)
        return sonuclar

    istemci = istemci_olustur()  # anahtar yoksa burada tek seferde AnalizHatasi fırlar
    max_paralel = max_paralel or int(os.getenv("AI_MAX_PARALEL", "4"))
    sonuclar: list[AnalizSonucu | None] = [None] * len(ilanlar)

    with ThreadPoolExecutor(max_workers=max(1, max_paralel)) as havuz:
        gorevler = {havuz.submit(arac_analiz_et, ilan, istemci, None, effort): idx for idx, ilan in enumerate(ilanlar)}
        for tamamlanan, gorev in enumerate(as_completed(gorevler), 1):
            idx = gorevler[gorev]
            try:
                sonuc = AnalizSonucu(ilan=ilanlar[idx], analiz=gorev.result())
            except AnalizHatasi as e:
                sonuc = AnalizSonucu(ilan=ilanlar[idx], hata=str(e))
            except Exception as e:  # beklenmeyen hatalar da tek ilanla sınırlı kalsın
                logger.exception("Beklenmeyen analiz hatası")
                sonuc = AnalizSonucu(ilan=ilanlar[idx], hata=f"Beklenmeyen hata: {e}")
            sonuclar[idx] = sonuc
            if ilerleme:
                ilerleme(tamamlanan, len(ilanlar), sonuc)

    return [s for s in sonuclar if s is not None]


# ---------------------------------------------------------------------------
# DEMO analiz (API anahtarı olmadan arayüzü test etmek için)
# ---------------------------------------------------------------------------

_DEMO_BILGI: dict[str, dict] = {
    "Egea": dict(arti=["Parçası ucuz ve her yerde bulunur", "Geniş bagaj"], eksi=["Yalıtım zayıf", "Malzeme kalitesi vasat"],
                 kronik=["Ön takım burç / rot başı aşınması", "Multijet'te EGR ve DPF tıkanması"], maliyet="Ucuz"),
    "Clio": dict(arti=["Şehir içi pratik", "Düşük yakıt tüketimi"], eksi=["Arka diz mesafesi dar"],
                 kronik=["Multimedya ekran donmaları", "1.0 TCe'de düşük devirde titreme"], maliyet="Ortalama"),
    "Megane": dict(arti=["Yakıt cimrisi 1.5 dCi", "Geniş bagaj"], eksi=["İç malzeme kalitesi ortalama"],
                   kronik=["1.5 dCi enjektör sorunları", "EDC şanzıman kavrama aşınması"], maliyet="Ortalama"),
    "Golf": dict(arti=["İkinci eli hızlı", "Sürüş kalitesi yüksek"], eksi=["Fiyatı segmentine göre yüksek"],
                 kronik=["1.2 TSI'da zincir/kayış ve yağ eksiltme (eski kuşak)", "DSG mekatronik arızası"], maliyet="Pahalı"),
    "Corolla": dict(arti=["Çok sağlam mekanik", "İkinci eli hızlı"], eksi=["Performans zayıf", "Yalıtım ortalama"],
                    kronik=["Bilinen ciddi bir kronik sorunu az; CVT'de ağır bakım ihmali sorun çıkarabilir"], maliyet="Ortalama"),
    "Passat": dict(arti=["Geniş ve konforlu", "Uzun yol aracı"], eksi=["Parça ve bakım pahalı"],
                   kronik=["DSG mekatronik", "Ön takım salıncak burçları"], maliyet="Pahalı"),
    "Duster": dict(arti=["Dayanıklı ve basit mekanik", "Yüksek yerden yükseklik"], eksi=["Yalıtım zayıf", "Güvenlik donanımı az"],
                   kronik=["Pas oluşumu (eski kasa)", "Debriyaj seti erken aşınma"], maliyet="Ucuz"),
    "Doblo": dict(arti=["Çok geniş iç hacim", "Ticari kullanıma uygun"], eksi=["Konfor düşük"],
                  kronik=["Ön takım ve arka makas sesleri"], maliyet="Ucuz"),
}


def demo_analiz(ilan: AracIlani) -> AracAnalizi:
    """API çağrısı yapmadan kurallara dayalı ÖRNEK analiz üretir. Gerçek AI değerlendirmesi değildir."""
    bilgi = next((v for k, v in _DEMO_BILGI.items() if k.lower() in (ilan.seri or "").lower()), None) or dict(
        arti=["Segmentinde yaygın bir model"], eksi=["Detaylı bilgi için gerçek AI analizini kullanın"],
        kronik=["Demo modunda bu model için kayıtlı bilgi yok"], maliyet="Ortalama",
    )
    yas = 2026 - ilan.yil if ilan.yil else 10
    yillik_km = (ilan.km or 0) / max(yas, 1)
    durum = ilan.hasar.durum

    sorunlar = []
    if durum == "Ağır hasar kayıtlı":
        sorunlar.append("ağır hasar kaydı var")
    if ilan.hasar.tramer_tutari and ilan.hasar.tramer_tutari > 40_000:
        sorunlar.append("tramer tutarı yüksek")
    if yillik_km > 25_000:
        sorunlar.append("yıllık kilometresi yüksek")
    if (ilan.km or 0) > 250_000:
        sorunlar.append("toplam kilometresi çok yüksek")
    alinir = not sorunlar

    ozet = (
        f"[DEMO] {ilan.model_adi}, {km_formatla(ilan.km)} ve {tl_formatla(ilan.fiyat)} fiyatla listelenmiş. "
        + ("Belirgin bir risk görünmüyor; yine de ekspertiz yaptırın." if alinir
           else f"Dikkat: {', '.join(sorunlar)}.")
        + " Bu metin kurallarla üretilmiş örnek bir analizdir; gerçek değerlendirme için API anahtarı ekleyin."
    )
    eksiler = list(bilgi["eksi"])
    if durum == "Belirtilmemiş":
        eksiler.append("İlanda hasar/tramer bilgisi belirtilmemiş")
    return AracAnalizi(
        alinir_mi=alinir,
        genel_degerlendirme=ozet,
        artilari=list(bilgi["arti"]),
        eksileri=eksiler,
        kronik_sorunlar=list(bilgi["kronik"]),
        parca_ve_bakim_maliyeti=f"{bilgi['maliyet']} (demo verisi)",
    )


if __name__ == "__main__":
    # Hızlı deneme:  python ai_analyzer.py        -> ilk mock ilanı Claude ile analiz eder
    #                python ai_analyzer.py demo   -> API çağrısı olmadan demo analiz
    import sys

    from scraper import AramaKriterleri, mock_ilanlar_uret

    ilan = mock_ilanlar_uret(AramaKriterleri(max_butce=1_500_000), adet=1, seed=7)[0]
    print(prompt_olustur(ilan), "\n" + "-" * 60)
    try:
        sonuc = demo_analiz(ilan) if "demo" in sys.argv else arac_analiz_et(ilan)
        print(sonuc.model_dump_json(indent=2))
    except AnalizHatasi as e:
        print(f"HATA: {e}")
