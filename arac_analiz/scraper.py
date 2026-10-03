"""
Veri çekme (scraper) modülü.

İçerik:
  * AracIlani / HasarBilgisi / AramaKriterleri veri sınıfları
  * hasar_bilgisi_cikar()      -> İlan açıklamasından Tramer / Hasar kaydı bilgisini regex ile ayıklar
  * mock_ilanlar_uret()        -> Test için sahte (dummy) ilan üretir; internet / bot koruması gerekmez
  * sahibinden_ilanlari_cek()  -> Playwright ile sahibinden.com'dan gerçek ilan çeker
  * ilanlari_getir()           -> Seçilen kaynağa göre ilanları döndüren tek giriş noktası

Not: sahibinden.com güçlü bot korumasına (Cloudflare + kendi kontrolleri) sahiptir ve HTML yapısı
zaman zaman değişir. Gerçek scraper engellenirse SahibindenEngelHatasi fırlatır; arayüz bu durumda
mock veriye geri düşebilir. Siteyi yoğun şekilde taramak kullanım koşullarına aykırı olabilir;
düşük hacimde ve kişisel amaçla kullanın.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlencode, urljoin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

ARAC_TURLERI = ["Binek", "SUV", "Ticari"]
KASA_TIPLERI = ["Farketmez", "Sedan", "Hatchback", "Station Wagon"]

SAHIBINDEN_KOK = "https://www.sahibinden.com"
KATEGORI_YOLLARI = {
    "Binek": "/otomobil",
    "SUV": "/arazi-suv-pickup",
    "Ticari": "/minivan-van_panelvan",
}
SAYFA_BASINA_ILAN = 20

# Gerçek tarayıcılara ait güncel user-agent havuzu (her oturumda rastgele biri seçilir)
USER_AGENTLAR = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0",
]

# Engellenme / doğrulama sayfalarını tanımak için kullanılan ipuçları
ENGEL_IPUCLARI = [
    "just a moment",
    "attention required",
    "cf-challenge",
    "captcha",
    "olağan dışı",
    "olağandışı",
    "güvenlik doğrulaması",
    "erişiminiz engellendi",
]


# ---------------------------------------------------------------------------
# Hatalar
# ---------------------------------------------------------------------------

class ScraperHatasi(Exception):
    """Veri çekme sırasında oluşan genel hata."""


class SahibindenEngelHatasi(ScraperHatasi):
    """Site bot korumasına takıldığımızda (captcha, Cloudflare vb.) fırlatılır."""


# ---------------------------------------------------------------------------
# Veri sınıfları
# ---------------------------------------------------------------------------

@dataclass
class AramaKriterleri:
    max_butce: int
    arac_turu: str = "Binek"
    kasa_tipi: str = "Farketmez"

    def __post_init__(self) -> None:
        if self.max_butce <= 0:
            raise ValueError("Maksimum bütçe sıfırdan büyük olmalıdır.")
        if self.arac_turu not in ARAC_TURLERI:
            raise ValueError(f"Geçersiz araç türü: {self.arac_turu}")
        if self.kasa_tipi not in KASA_TIPLERI:
            raise ValueError(f"Geçersiz kasa tipi: {self.kasa_tipi}")


@dataclass
class HasarBilgisi:
    durum: str = "Belirtilmemiş"  # "Hasar kaydı yok" | "Hasar kaydı var" | "Ağır hasar kayıtlı" | "Belirtilmemiş"
    tramer_tutari: int | None = None
    boya_degisen: list[str] = field(default_factory=list)
    kanitlar: list[str] = field(default_factory=list)  # açıklamada eşleşen cümle parçaları

    @property
    def ozet(self) -> str:
        parcalar = [self.durum]
        if self.tramer_tutari:
            parcalar.append(f"Tramer: {tl_formatla(self.tramer_tutari)}")
        if self.boya_degisen:
            parcalar.append(", ".join(self.boya_degisen))
        return " · ".join(parcalar)


@dataclass
class AracIlani:
    baslik: str
    fiyat: int | None
    marka: str = ""
    seri: str = ""
    model: str = ""
    yil: int | None = None
    km: int | None = None
    link: str = ""
    aciklama: str = ""
    resim_url: str = ""
    kasa_tipi: str = ""
    kaynak: str = "mock"
    hasar: HasarBilgisi = field(default_factory=HasarBilgisi)

    def __post_init__(self) -> None:
        # Açıklama varsa ve hasar bilgisi henüz çıkarılmadıysa otomatik çıkar
        if self.aciklama and self.hasar.durum == "Belirtilmemiş" and not self.hasar.kanitlar:
            self.hasar = hasar_bilgisi_cikar(self.aciklama)

    @property
    def model_adi(self) -> str:
        """Örn: '2016 Volkswagen Golf 1.2 TSI Comfortline'"""
        parcalar = [str(self.yil) if self.yil else "", self.marka, self.seri, self.model]
        ad = " ".join(p for p in parcalar if p).strip()
        return ad or self.baslik

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hasar_ozeti"] = self.hasar.ozet
        return d


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------

def tl_formatla(tutar: int | None) -> str:
    if tutar is None:
        return "Belirtilmemiş"
    return f"{tutar:,.0f} TL".replace(",", ".")


def km_formatla(km: int | None) -> str:
    if km is None:
        return "Belirtilmemiş"
    return f"{km:,.0f} km".replace(",", ".")


def tr_kucuk(metin: str) -> str:
    """Türkçe karakterlere duyarlı küçük harfe çevirme (İ->i, I->ı)."""
    return metin.replace("İ", "i").replace("I", "ı").lower()


def sayi_ayikla(metin: str | None) -> int | None:
    """'1.250.000 TL' -> 1250000, '145.000 km' -> 145000. Sayı yoksa None."""
    if not metin:
        return None
    rakamlar = re.sub(r"[^\d]", "", metin)
    return int(rakamlar) if rakamlar else None


def _tutar_coz(sayi: str, carpan: str | None) -> int | None:
    """'12.500' -> 12500, '12,5' + 'bin' -> 12500, '45' + 'bin' -> 45000."""
    sayi = sayi.strip()
    try:
        if carpan:
            deger = float(sayi.replace(".", "").replace(",", ".")) if "," in sayi else float(sayi.replace(".", ""))
            return int(round(deger * 1000))
        # Binlik ayraçlı ("12.500" / "12,500") veya düz sayı
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+", sayi):
            return int(re.sub(r"[.,]", "", sayi))
        return int(float(sayi.replace(",", ".")))
    except ValueError:
        return None


# Tramer tutarı: "12.500 TL tramer", "tramer kaydı: 12.500 TL", "tramer 45 bin", "35k tramer"
_SAYI = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)"
_CARPAN = r"(bin|b|k)?"
_TRAMER_KELIME = r"(?:tramer|hasar\s*kayd[ıi]|hasar\s*kaydı)"
_TUTAR_ONCE = re.compile(
    _SAYI + r"\s*" + _CARPAN + r"\s*(?:tl|₺|lira)?\s*(?:'?l[ıi]k|'?lük)?\s*(?:bir\s*)?" + _TRAMER_KELIME
)
_TUTAR_SONRA = re.compile(
    _TRAMER_KELIME + r"\s*(?:kayd[ıi])?\s*(?:tutar[ıi])?\s*[:=\-–]?\s*(?:var\s*|mevcut\s*)?\(?\s*" + _SAYI + r"\s*" + _CARPAN
    + r"\s*(?:tl|₺|lira)?"
)

_HASAR_YOK = re.compile(
    r"(tramer\s*(?:kayd[ıi])?\s*(?:[:=\-–]\s*)?(?:yok|yoktur|bulunmamaktad[ıi]r|bulunmuyor|temiz|0\b|sıfır))"
    r"|((?<!ağır\s)hasar\s*kayd[ıi]\s*(?:[:=\-–]\s*)?(?:yok|yoktur|bulunmamaktad[ıi]r|bulunmuyor|temiz))"
    r"|tramersiz|hasar\s*kayıts[ıi]z|hasars[ıi]z|sıfır\s*tramer|tramer\s*temiz"
)
_HASAR_VAR = re.compile(
    r"(tramer\s*(?:kayd[ıi])?\s*(?:[:=\-–]\s*)?(?:var|mevcut|bulunmaktad[ıi]r))"
    r"|((?<!ağır\s)hasar\s*kayd[ıi]\s*(?:[:=\-–]\s*)?(?:var|mevcut|bulunmaktad[ıi]r))"
    r"|(tramerli)|((?<!ağır\s)hasar\s*kayıtl[ıi](?!\s*[:=\-–]\s*hay))"
)
_AGIR_HASAR_EVET = re.compile(
    r"ağır\s*hasar(?:\s*kayıtl[ıi])?\s*[:=\-–]\s*evet|ağır\s*hasarl[ıi]|ağır\s*hasar\s*kayd[ıi]\s*(?:var|mevcut)"
    r"|ağır\s*hasar\s*kayıtl[ıi]d[ıi]r"
)
_AGIR_HASAR_HAYIR = re.compile(r"ağır\s*hasar(?:\s*kayıtl[ıi])?\s*[:=\-–]\s*hay[ıi]r|ağır\s*hasar(?:\s*kayd[ıi])?\s*yok")

_BOYA_DEGISEN = [
    (re.compile(r"hatas[ıi]z\s*boyas[ıi]z|hatas[ıi]z"), "Hatasız"),
    (re.compile(r"boyas[ıi]z"), "Boyasız"),
    (re.compile(r"değişens[ıi]z"), "Değişensiz"),
    (re.compile(r"(\d+|bir|iki|üç|dört|beş)\s*parça\s*(?:lokal\s*)?boya(?:l[ıi])?"), None),  # "2 parça boyalı"
    (re.compile(r"lokal\s*boya"), "Lokal boya"),
    (re.compile(r"(\d+|bir|iki|üç|dört|beş)\s*parça\s*değişen"), None),
    (re.compile(r"komple\s*boya"), "Komple boyalı"),
]


def _cumle_bul(metin: str, bas: int, bit: int, pay: int = 40) -> str:
    return metin[max(0, bas - pay): min(len(metin), bit + pay)].strip()


def hasar_bilgisi_cikar(aciklama: str | None) -> HasarBilgisi:
    """
    İlan açıklamasından Tramer / Hasar kaydı bilgisini regex ile ayıklamaya çalışır.

    Öncelik sırası: Ağır hasar > Tramer tutarı > "tramer yok" ifadeleri > "tramer var" ifadeleri.
    Kesin bilgi bulunamazsa durum "Belirtilmemiş" olarak kalır.
    """
    bilgi = HasarBilgisi()
    if not aciklama:
        return bilgi

    metin = tr_kucuk(re.sub(r"\s+", " ", aciklama))

    # 1) Tramer tutarı
    tutarlar: list[int] = []
    for desen in (_TUTAR_ONCE, _TUTAR_SONRA):
        for m in desen.finditer(metin):
            tutar = _tutar_coz(m.group(1), m.group(2))
            # Yıl gibi sayıları (2016 tramer) yanlışlıkla tutar sanmamak için çok küçük değerleri ele
            if tutar is not None and (tutar == 0 or tutar >= 250):
                tutarlar.append(tutar)
                bilgi.kanitlar.append(_cumle_bul(metin, m.start(), m.end()))
    pozitif = [t for t in tutarlar if t > 0]
    if pozitif:
        bilgi.tramer_tutari = max(pozitif)

    # 2) Boya / değişen bilgisi
    for desen, etiket in _BOYA_DEGISEN:
        m = desen.search(metin)
        if not m:
            continue
        if etiket is None:
            etiket = m.group(0).strip().capitalize()
        if etiket not in bilgi.boya_degisen and not (etiket == "Boyasız" and "Hatasız" in bilgi.boya_degisen):
            bilgi.boya_degisen.append(etiket)

    # 3) Durum kararı
    agir_evet = _AGIR_HASAR_EVET.search(metin)
    if agir_evet and not _AGIR_HASAR_HAYIR.search(metin):
        bilgi.durum = "Ağır hasar kayıtlı"
        bilgi.kanitlar.append(_cumle_bul(metin, agir_evet.start(), agir_evet.end()))
    elif bilgi.tramer_tutari:
        bilgi.durum = "Hasar kaydı var"
    elif (m := _HASAR_YOK.search(metin)) or 0 in tutarlar:
        bilgi.durum = "Hasar kaydı yok"
        if m:
            bilgi.kanitlar.append(_cumle_bul(metin, m.start(), m.end()))
    elif m := _HASAR_VAR.search(metin):
        bilgi.durum = "Hasar kaydı var"
        bilgi.kanitlar.append(_cumle_bul(metin, m.start(), m.end()))

    # Tekrarlayan kanıtları temizle
    bilgi.kanitlar = list(dict.fromkeys(bilgi.kanitlar))
    return bilgi


def _kasa_eslesir_mi(ilan_kasa: str, istenen: str) -> bool:
    if istenen == "Farketmez" or not ilan_kasa:
        return True
    return tr_kucuk(istenen).replace(" ", "") in tr_kucuk(ilan_kasa).replace(" ", "")


# ---------------------------------------------------------------------------
# MOCK (sahte) veri üretici
# ---------------------------------------------------------------------------

# (marka, seri, model, kasa, tür, 2026'da ~3 yaşındaki örneğin yaklaşık fiyatı TL, ilk yıl, son yıl)
_MOCK_KATALOG = [
    ("Fiat", "Egea", "1.3 Multijet Easy", "Sedan", "Binek", 1_050_000, 2015, 2023),
    ("Fiat", "Egea", "1.4 Fire Urban", "Hatchback", "Binek", 1_000_000, 2016, 2023),
    ("Fiat", "Egea", "1.6 Multijet Lounge", "Station Wagon", "Binek", 1_200_000, 2016, 2023),
    ("Renault", "Clio", "1.0 TCe Touch", "Hatchback", "Binek", 1_050_000, 2020, 2023),
    ("Renault", "Megane", "1.5 dCi Touch", "Sedan", "Binek", 1_350_000, 2016, 2023),
    ("Volkswagen", "Golf", "1.2 TSI Comfortline", "Hatchback", "Binek", 1_600_000, 2013, 2019),
    ("Volkswagen", "Passat", "1.6 TDI BlueMotion Comfortline", "Sedan", "Binek", 2_100_000, 2015, 2022),
    ("Volkswagen", "Passat Variant", "1.6 TDI Highline", "Station Wagon", "Binek", 2_200_000, 2015, 2022),
    ("Toyota", "Corolla", "1.6 Vision", "Sedan", "Binek", 1_450_000, 2013, 2019),
    ("Toyota", "Auris", "1.33 Life", "Hatchback", "Binek", 1_200_000, 2010, 2018),
    ("Hyundai", "i20", "1.4 MPI Jump", "Hatchback", "Binek", 950_000, 2015, 2023),
    ("Honda", "Civic", "1.6 i-VTEC Eco Elegance", "Sedan", "Binek", 1_500_000, 2016, 2021),
    ("Skoda", "Octavia", "1.6 TDI Style", "Sedan", "Binek", 1_550_000, 2013, 2020),
    ("Skoda", "Octavia Combi", "1.6 TDI Style", "Station Wagon", "Binek", 1_650_000, 2013, 2020),
    ("Opel", "Astra", "1.6 CDTI Enjoy", "Hatchback", "Binek", 1_150_000, 2016, 2021),
    ("Peugeot", "308 SW", "1.5 BlueHDi Active", "Station Wagon", "Binek", 1_350_000, 2018, 2022),
    ("Dacia", "Duster", "1.5 dCi Laureate", "SUV", "SUV", 1_250_000, 2010, 2023),
    ("Nissan", "Qashqai", "1.5 dCi Tekna", "SUV", "SUV", 1_650_000, 2014, 2021),
    ("Peugeot", "3008", "1.5 BlueHDi Allure", "SUV", "SUV", 1_900_000, 2018, 2023),
    ("Hyundai", "Tucson", "1.6 CRDi Elite", "SUV", "SUV", 2_000_000, 2016, 2021),
    ("Renault", "Kadjar", "1.5 dCi Icon", "SUV", "SUV", 1_550_000, 2015, 2022),
    ("Fiat", "Doblo", "1.6 Multijet Safeline", "Kombi", "Ticari", 1_000_000, 2010, 2022),
    ("Ford", "Tourneo Courier", "1.5 TDCi Journey Trend", "Kombi", "Ticari", 950_000, 2014, 2023),
    ("Renault", "Kangoo", "1.5 dCi Touch", "Kombi", "Ticari", 900_000, 2010, 2021),
    ("Volkswagen", "Caddy", "2.0 TDI Exclusive", "Kombi", "Ticari", 1_300_000, 2016, 2020),
    ("Ford", "Transit Custom", "2.0 TDCi 320 L", "Panelvan", "Ticari", 1_500_000, 2016, 2023),
]

_MOCK_ACIKLAMALAR = [
    "Aracım {yil} model, {km} km'dedir. Tramer kaydı yoktur, hatasız boyasızdır. "
    "Bakımları yetkili serviste zamanında yapıldı. Ciddi alıcılar arasın.",
    "Sahibinden temiz {marka} {seri}. {tramer} TL tramer kaydı mevcut, 2 parça boyalı, değişen yok. "
    "Lastikler yeni, ağır hasar kaydı yoktur.",
    "Aile aracı olarak kullanıldı. Tramer kaydı: {tramer} TL. Sol arka çamurluk lokal boya. "
    "Muayenesi yeni yapıldı, takas düşünülmez.",
    "{km} km, servis bakımlı. Tramersiz, boyasız, değişensiz. Expertiz raporu mevcuttur.",
    "Araç {yil} çıkışlı, hasar kaydı var ({tramer} TL), 1 parça değişen var (ön kaput). "
    "Mekanik olarak sorunsuz, yakıt cimrisi.",
    "Acil satılık! Fiyatta pazarlık payı vardır. Detaylı bilgi için arayınız.",
    "Ağır hasar kayıtlıdır, bilgilerinize. Onarımı yapıldı, sorunsuz kullanılıyor. {km} km.",
]


_RESIM_RENKLERI = ["#1f2937", "#1e3a8a", "#065f46", "#7c2d12", "#4c1d95", "#374151"]


def _placeholder_resim(marka: str, seri: str) -> str:
    """Mock ilanlar için internet gerektirmeyen SVG görsel (st.image SVG metnini doğrudan çizer)."""
    renk = _RESIM_RENKLERI[sum(map(ord, marka + seri)) % len(_RESIM_RENKLERI)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300" width="640" height="300">'
        f'<rect width="640" height="300" fill="{renk}"/>'
        '<text x="320" y="150" font-size="96" text-anchor="middle">🚗</text>'
        f'<text x="320" y="230" font-family="sans-serif" font-size="40" font-weight="700" fill="#f9fafb" '
        f'text-anchor="middle">{marka} {seri}</text>'
        '<text x="320" y="270" font-family="sans-serif" font-size="20" fill="#d1d5db" '
        'text-anchor="middle">Demo ilan görseli</text></svg>'
    )


def mock_ilanlar_uret(kriter: AramaKriterleri, adet: int = 12, seed: int | None = None) -> list[AracIlani]:
    """
    Sahibinden'e bağlanmadan, kriterlere uygun gerçekçi görünen sahte ilanlar üretir.
    Projeyi internet erişimi / bot koruması olmadan uçtan uca test etmek içindir.
    """
    rng = random.Random(seed)
    adaylar = [
        k for k in _MOCK_KATALOG
        if k[4] == kriter.arac_turu and (kriter.arac_turu != "Binek" or _kasa_eslesir_mi(k[3], kriter.kasa_tipi))
    ]
    if not adaylar:
        return []

    ilanlar: list[AracIlani] = []
    deneme = 0
    while len(ilanlar) < adet and deneme < adet * 30:
        deneme += 1
        marka, seri, model, kasa, _, referans_fiyat, ilk_yil, son_yil = rng.choice(adaylar)
        yil = rng.randint(ilk_yil, son_yil)
        yas = 2026 - yil
        km = max(5_000, int(yas * rng.randint(11_000, 24_000) / 1000) * 1000)
        # Basit amortisman: yıllık ~%7 değer kaybı + km etkisi + rastgele piyasa sapması
        fiyat = referans_fiyat * (0.93 ** (yas - 3)) * (1 - min(km, 300_000) / 1_500_000)
        fiyat *= rng.uniform(0.88, 1.12)
        fiyat = int(round(fiyat / 5_000) * 5_000)
        if fiyat > kriter.max_butce or fiyat < kriter.max_butce * 0.35:
            continue

        tramer = rng.choice([4_500, 8_750, 12_500, 18_000, 27_300, 41_000, 65_000])
        sablon = rng.choice(_MOCK_ACIKLAMALAR)
        aciklama = sablon.format(
            yil=yil, km=km_formatla(km).replace(" km", ""), marka=marka, seri=seri,
            tramer=f"{tramer:,}".replace(",", "."),
        )
        ilan_no = rng.randint(1_100_000_000, 1_299_999_999)
        ilanlar.append(
            AracIlani(
                baslik=f"SAHİBİNDEN {yil} {marka.upper()} {seri.upper()} {model.upper()}",
                fiyat=fiyat,
                marka=marka,
                seri=seri,
                model=model,
                yil=yil,
                km=km,
                link=f"{SAHIBINDEN_KOK}/ilan/vasita-ornek-{ilan_no}/detay",
                aciklama=aciklama,
                resim_url=_placeholder_resim(marka, seri),
                kasa_tipi=kasa,
                kaynak="mock",
            )
        )

    ilanlar.sort(key=lambda i: i.fiyat or 0)
    return ilanlar


# ---------------------------------------------------------------------------
# GERÇEK scraper (Playwright)
# ---------------------------------------------------------------------------

def arama_url_olustur(kriter: AramaKriterleri, sayfa: int = 1) -> str:
    """
    Kriterlere uygun sahibinden.com arama URL'si üretir.

    Kasa tipi filtresi sahibinden'de kategoriye özel sayısal kodlarla tutulduğu ve bu kodlar
    değişebildiği için URL'ye eklenmez; ilan detayındaki "Kasa Tipi" alanına göre sonradan filtrelenir.
    """
    yol = KATEGORI_YOLLARI[kriter.arac_turu]
    parametreler = {
        "price_max": kriter.max_butce,
        "pagingSize": SAYFA_BASINA_ILAN,
    }
    if sayfa > 1:
        parametreler["pagingOffset"] = (sayfa - 1) * SAYFA_BASINA_ILAN
    return f"{SAHIBINDEN_KOK}{yol}?{urlencode(parametreler)}"


def _rastgele_bekle(alt: float = 1.5, ust: float = 4.0) -> None:
    time.sleep(random.uniform(alt, ust))


def _engel_kontrol(page) -> None:
    try:
        baslik = tr_kucuk(page.title() or "")
        url = page.url.lower()
        govde = tr_kucuk(page.locator("body").inner_text(timeout=5_000)[:3_000])
    except Exception:  # sayfa tamamen boşsa / kapanmışsa
        baslik, url, govde = "", page.url.lower(), ""
    if "secure.sahibinden.com" in url or any(i in baslik or i in govde[:1_500] for i in ENGEL_IPUCLARI):
        raise SahibindenEngelHatasi(
            "sahibinden.com bot korumasına takıldı (doğrulama / captcha sayfası). "
            "Daha sonra tekrar deneyin, headless modu kapatın ya da mock veri kullanın."
        )


_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


def _liste_satirlarini_oku(page) -> list[dict]:
    """Arama sonuç tablosundaki satırları JS ile tek seferde okur (daha hızlı ve dayanıklı)."""
    return page.evaluate(
        """
        () => Array.from(document.querySelectorAll('tr.searchResultsItem'))
          .filter(tr => tr.getAttribute('data-id') && !tr.classList.contains('nativeAd'))
          .map(tr => {
            const a = tr.querySelector('a.classifiedTitle') || tr.querySelector('td.searchResultsTitleValue a');
            const img = tr.querySelector('td.searchResultsLargeThumbnail img, img');
            const tags = Array.from(tr.querySelectorAll('td.searchResultsTagAttributeValue'))
                              .map(td => td.innerText.trim());
            const attrs = Array.from(tr.querySelectorAll('td.searchResultsAttributeValue'))
                              .map(td => td.innerText.trim());
            const price = tr.querySelector('td.searchResultsPriceValue');
            return {
              baslik: a ? a.innerText.trim() : '',
              href: a ? a.getAttribute('href') : '',
              resim: img ? (img.getAttribute('data-src') || img.getAttribute('src') || '') : '',
              etiketler: tags,
              ozellikler: attrs,
              fiyat: price ? price.innerText.trim() : '',
            };
          });
        """
    )


def _satirdan_ilan(satir: dict) -> AracIlani | None:
    if not satir.get("href") or not satir.get("baslik"):
        return None
    yil = km = None
    for deger in satir.get("ozellikler", []):
        sade = deger.replace(".", "").replace(" ", "")
        if yil is None and re.fullmatch(r"(19|20)\d{2}", sade):
            yil = int(sade)
        elif km is None and re.fullmatch(r"\d{1,7}", sade) and ("." in deger or int(sade) > 3000):
            km = int(sade)
    etiketler = satir.get("etiketler", [])
    return AracIlani(
        baslik=satir["baslik"],
        fiyat=sayi_ayikla(satir.get("fiyat")),
        seri=etiketler[0] if etiketler else "",
        model=etiketler[1] if len(etiketler) > 1 else "",
        yil=yil,
        km=km,
        link=urljoin(SAHIBINDEN_KOK, satir["href"]),
        resim_url=satir.get("resim", ""),
        kaynak="sahibinden",
    )


def _detay_doldur(page, ilan: AracIlani) -> None:
    """İlan detay sayfasından Marka/Seri/Model/Yıl/KM/Kasa Tipi, açıklama ve büyük resmi okur."""
    page.goto(ilan.link, wait_until="domcontentloaded", timeout=45_000)
    _engel_kontrol(page)
    veri = page.evaluate(
        """
        () => {
          const bilgi = {};
          document.querySelectorAll('ul.classifiedInfoList li').forEach(li => {
            const k = li.querySelector('strong'); const v = li.querySelector('span');
            if (k && v) bilgi[k.innerText.trim()] = v.innerText.trim();
          });
          const acik = document.querySelector('#classifiedDescription');
          const og = document.querySelector('meta[property="og:image"]');
          const foto = document.querySelector('.classifiedDetailMainPhoto img, img.stdImg');
          return {
            bilgi,
            aciklama: acik ? acik.innerText.trim() : '',
            resim: og ? og.content : (foto ? (foto.getAttribute('data-src') || foto.src) : ''),
          };
        }
        """
    )
    bilgi: dict = veri.get("bilgi", {})
    ilan.marka = bilgi.get("Marka", ilan.marka)
    ilan.seri = bilgi.get("Seri", ilan.seri)
    ilan.model = bilgi.get("Model", ilan.model)
    ilan.yil = sayi_ayikla(bilgi.get("Yıl")) or ilan.yil
    ilan.km = sayi_ayikla(bilgi.get("KM")) or ilan.km
    ilan.kasa_tipi = bilgi.get("Kasa Tipi", ilan.kasa_tipi)
    ilan.resim_url = veri.get("resim") or ilan.resim_url

    aciklama = veri.get("aciklama", "")
    # Detaydaki yapılandırılmış "Ağır Hasar Kayıtlı" alanını da hasar analizine dahil et
    if "Ağır Hasar Kayıtlı" in bilgi:
        aciklama += f"\nAğır Hasar Kayıtlı: {bilgi['Ağır Hasar Kayıtlı']}"
    ilan.aciklama = aciklama
    ilan.hasar = hasar_bilgisi_cikar(aciklama)


def _sahibinden_cek_senkron(
    kriter: AramaKriterleri, sayfa_sayisi: int, max_ilan: int, headless: bool, detay_cek: bool
) -> list[AracIlani]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScraperHatasi("Playwright kurulu değil: pip install playwright && playwright install chromium") from e

    ilanlar: list[AracIlani] = []
    try:
        with sync_playwright() as p:
            tarayici = p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                # İsteğe bağlı: sistemdeki Chrome/Chromium'u kullanmak için .env'de PLAYWRIGHT_CHROMIUM_PATH tanımlayın
                executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None,
            )
            try:
                context = tarayici.new_context(
                    user_agent=random.choice(USER_AGENTLAR),
                    locale="tr-TR",
                    timezone_id="Europe/Istanbul",
                    viewport={"width": random.randint(1280, 1600), "height": random.randint(760, 960)},
                    extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
                )
                context.add_init_script(_STEALTH_JS)
                page = context.new_page()

                # Önce ana sayfayı ziyaret et (doğal gezinme + çerezler)
                page.goto(SAHIBINDEN_KOK, wait_until="domcontentloaded", timeout=45_000)
                _engel_kontrol(page)
                _rastgele_bekle(2, 4)

                for sayfa in range(1, sayfa_sayisi + 1):
                    url = arama_url_olustur(kriter, sayfa)
                    logger.info("Arama sayfası açılıyor: %s", url)
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    _engel_kontrol(page)
                    try:
                        page.wait_for_selector("tr.searchResultsItem", timeout=15_000)
                    except PlaywrightTimeout:
                        _engel_kontrol(page)
                        logger.warning("Sayfa %s'de ilan bulunamadı.", sayfa)
                        break
                    # İnsan benzeri davranış: biraz kaydır
                    page.mouse.wheel(0, random.randint(400, 1400))
                    _rastgele_bekle(1, 2.5)

                    for satir in _liste_satirlarini_oku(page):
                        ilan = _satirdan_ilan(satir)
                        if ilan and (ilan.fiyat is None or ilan.fiyat <= kriter.max_butce):
                            ilanlar.append(ilan)
                    if len(ilanlar) >= max_ilan:
                        break
                    _rastgele_bekle(3, 6)

                ilanlar = ilanlar[:max_ilan]

                if detay_cek:
                    for i, ilan in enumerate(ilanlar):
                        try:
                            _rastgele_bekle(2, 5)
                            _detay_doldur(page, ilan)
                        except SahibindenEngelHatasi:
                            if i == 0:
                                raise
                            logger.warning("Detay sayfalarında engellendi; %s ilan detaysız kaldı.", len(ilanlar) - i)
                            break
                        except (PlaywrightTimeout, PlaywrightError) as e:
                            logger.warning("Detay okunamadı (%s): %s", ilan.link, e)
            finally:
                tarayici.close()
    except ScraperHatasi:
        raise
    except Exception as e:  # Playwright / ağ hataları
        mesaj = str(e)
        if "Executable doesn't exist" in mesaj:
            raise ScraperHatasi("Chromium bulunamadı. Lütfen çalıştırın: playwright install chromium") from e
        ilk_satir = mesaj.splitlines()[0] if mesaj else repr(e)
        raise ScraperHatasi(f"Sahibinden'den veri çekilemedi: {ilk_satir}") from e

    if kriter.kasa_tipi != "Farketmez":
        ilanlar = [i for i in ilanlar if _kasa_eslesir_mi(i.kasa_tipi, kriter.kasa_tipi)]
    return ilanlar


def sahibinden_ilanlari_cek(
    kriter: AramaKriterleri,
    sayfa_sayisi: int = 1,
    max_ilan: int = 20,
    headless: bool = True,
    detay_cek: bool = True,
) -> list[AracIlani]:
    """
    sahibinden.com'dan ilk 1-2 sayfadaki ilanları çeker.

    Playwright'ın senkron API'si, çalışan bir asyncio döngüsü olan thread'lerde (ör. bazı Streamlit
    kurulumları) hata verdiği için işlem ayrı bir thread'de yürütülür.
    """
    sayfa_sayisi = max(1, min(sayfa_sayisi, 2))
    sonuc: dict = {}

    def _calistir() -> None:
        if sys.platform == "win32":
            # Windows'ta Playwright alt süreç açabilmek için Proactor döngüsüne ihtiyaç duyar
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            sonuc["ilanlar"] = _sahibinden_cek_senkron(kriter, sayfa_sayisi, max_ilan, headless, detay_cek)
        except BaseException as e:  # hatayı ana thread'e taşı
            sonuc["hata"] = e

    t = threading.Thread(target=_calistir, name="sahibinden-scraper", daemon=True)
    t.start()
    t.join()
    if "hata" in sonuc:
        raise sonuc["hata"]
    return sonuc.get("ilanlar", [])


# ---------------------------------------------------------------------------
# Tek giriş noktası
# ---------------------------------------------------------------------------

def ilanlari_getir(
    kriter: AramaKriterleri,
    kaynak: str = "mock",
    max_ilan: int = 12,
    sayfa_sayisi: int = 1,
    headless: bool = True,
) -> list[AracIlani]:
    """kaynak: 'mock' veya 'sahibinden'"""
    if kaynak == "sahibinden":
        return sahibinden_ilanlari_cek(kriter, sayfa_sayisi=sayfa_sayisi, max_ilan=max_ilan, headless=headless)
    return mock_ilanlar_uret(kriter, adet=max_ilan)


if __name__ == "__main__":
    # Hızlı deneme:  python scraper.py            -> mock veri
    #                python scraper.py gercek     -> sahibinden.com (engellenebilir)
    logging.basicConfig(level=logging.INFO)
    k = AramaKriterleri(max_butce=1_200_000, arac_turu="Binek", kasa_tipi="Farketmez")
    kaynak = "sahibinden" if len(sys.argv) > 1 and sys.argv[1] == "gercek" else "mock"
    try:
        for ilan in ilanlari_getir(k, kaynak=kaynak, max_ilan=8, headless=True):
            print(f"{ilan.model_adi:<55} {tl_formatla(ilan.fiyat):>14} {km_formatla(ilan.km):>12}  {ilan.hasar.ozet}")
    except ScraperHatasi as e:
        print(f"HATA: {e}")
