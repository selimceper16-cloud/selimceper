"""
Fiyat-Performans Araç Öneri ve Analiz Uygulaması — Streamlit arayüzü.

Çalıştırma:  streamlit run app.py
"""

from __future__ import annotations

import logging
import statistics

import streamlit as st

from ai_analyzer import (
    AnalizHatasi,
    AnalizSonucu,
    VARSAYILAN_EFFORT,
    api_anahtari_var_mi,
    toplu_analiz,
)
from scraper import (
    ARAC_TURLERI,
    KASA_TIPLERI,
    AracIlani,
    AramaKriterleri,
    SahibindenEngelHatasi,
    ScraperHatasi,
    ilanlari_getir,
    km_formatla,
    mock_ilanlar_uret,
    tl_formatla,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="Fiyat-Performans Araç Analizi", page_icon="🚗", layout="wide")

KAYNAK_MOCK = "Demo veri (mock)"
KAYNAK_GERCEK = "sahibinden.com (gerçek)"
ANALIZ_AI = "Claude AI (API)"
ANALIZ_DEMO = "Demo analiz (API'siz)"

HASAR_RENK = {
    "Hasar kaydı yok": "green",
    "Hasar kaydı var": "orange",
    "Ağır hasar kayıtlı": "red",
    "Belirtilmemiş": "gray",
}


# ---------------------------------------------------------------------------
# Kenar çubuğu: ayarlar
# ---------------------------------------------------------------------------

def kenar_cubugu() -> dict:
    with st.sidebar:
        st.header("⚙️ Ayarlar")

        kaynak = st.radio(
            "Veri kaynağı", [KAYNAK_MOCK, KAYNAK_GERCEK],
            help="Gerçek scraper bot korumasına takılabilir; projeyi test etmek için demo veriyi kullanın.",
        )
        sayfa_sayisi, headless, mock_yedek = 1, True, True
        if kaynak == KAYNAK_GERCEK:
            sayfa_sayisi = st.select_slider("Taranacak sayfa sayısı", options=[1, 2], value=1)
            headless = st.checkbox("Tarayıcıyı gizli çalıştır (headless)", value=True,
                                   help="Engellenirseniz kapatmayı deneyin; tarayıcı penceresi açılır.")
            mock_yedek = st.checkbox("Engellenirse demo veriye geç", value=True)

        max_ilan = st.slider("Analiz edilecek maksimum ilan", min_value=1, max_value=20, value=6,
                             help="Her ilan için ayrı bir AI çağrısı yapılır; maliyet/süre ilan sayısıyla artar.")

        st.divider()
        anahtar_var = api_anahtari_var_mi()
        analiz_modu = st.radio(
            "Analiz modu", [ANALIZ_AI, ANALIZ_DEMO], index=0 if anahtar_var else 1,
            help="Demo analiz, API anahtarı olmadan arayüzü denemek için kurallarla örnek sonuç üretir.",
        )
        if analiz_modu == ANALIZ_AI and not anahtar_var:
            st.warning("`.env` dosyasında `ANTHROPIC_API_KEY` bulunamadı. Demo analiz modunu seçin "
                       "veya anahtarı ekleyip uygulamayı yeniden başlatın.")
        effort = st.select_slider(
            "Analiz derinliği (effort)", options=["low", "medium", "high"], value=VARSAYILAN_EFFORT,
            disabled=analiz_modu != ANALIZ_AI,
            help="Yüksek derinlik daha ayrıntılı ama daha yavaş ve maliyetlidir.",
        )

    return dict(kaynak=kaynak, sayfa_sayisi=sayfa_sayisi, headless=headless, mock_yedek=mock_yedek,
                max_ilan=max_ilan, analiz_modu=analiz_modu, effort=effort)


# ---------------------------------------------------------------------------
# Arama formu
# ---------------------------------------------------------------------------

def arama_formu() -> AramaKriterleri | None:
    with st.form("arama_formu"):
        c1, c2, c3 = st.columns(3)
        butce = c1.number_input("Maksimum Bütçe (TL)", min_value=50_000, max_value=50_000_000,
                                value=1_000_000, step=50_000, format="%d")
        arac_turu = c2.selectbox("Araç Türü", ARAC_TURLERI)
        kasa_tipi = c3.selectbox("Kasa Tipi", KASA_TIPLERI,
                                 help="Kasa tipi filtresi Binek araçlarda uygulanır.")
        gonder = st.form_submit_button("🔍 Araçları Bul ve Analiz Et", type="primary", width="stretch")

    if not gonder:
        return None
    try:
        return AramaKriterleri(max_butce=int(butce), arac_turu=arac_turu, kasa_tipi=kasa_tipi)
    except ValueError as e:
        st.error(str(e))
        return None


# ---------------------------------------------------------------------------
# İş akışı: veri çek -> analiz et
# ---------------------------------------------------------------------------

def ilanlari_cek(kriter: AramaKriterleri, ayar: dict) -> tuple[list[AracIlani], str]:
    """İlanları çeker; gerçek kaynak engellenirse (ayara göre) mock veriye geri düşer."""
    if ayar["kaynak"] == KAYNAK_MOCK:
        return mock_ilanlar_uret(kriter, adet=ayar["max_ilan"]), "mock"

    with st.status("sahibinden.com taranıyor… (rastgele beklemeler nedeniyle 1-2 dakika sürebilir)",
                   expanded=False) as durum:
        try:
            ilanlar = ilanlari_getir(kriter, kaynak="sahibinden", max_ilan=ayar["max_ilan"],
                                     sayfa_sayisi=ayar["sayfa_sayisi"], headless=ayar["headless"])
            durum.update(label=f"{len(ilanlar)} ilan çekildi.", state="complete")
            return ilanlar, "sahibinden"
        except SahibindenEngelHatasi as e:
            durum.update(label="Bot korumasına takıldı.", state="error")
            hata = e
        except ScraperHatasi as e:
            durum.update(label="Veri çekilemedi.", state="error")
            hata = e

    if ayar["mock_yedek"]:
        st.warning(f"{hata}\n\nBunun yerine **demo (mock) veri** gösteriliyor.")
        return mock_ilanlar_uret(kriter, adet=ayar["max_ilan"]), "mock"
    st.error(str(hata))
    return [], "sahibinden"


def analiz_et(ilanlar: list[AracIlani], ayar: dict) -> list[AnalizSonucu]:
    demo = ayar["analiz_modu"] == ANALIZ_DEMO
    cubuk = st.progress(0.0, text="İlanlar yapay zeka ile analiz ediliyor…")

    def ilerleme(tamamlanan: int, toplam: int, sonuc: AnalizSonucu) -> None:
        cubuk.progress(tamamlanan / toplam, text=f"Analiz: {tamamlanan}/{toplam} — {sonuc.ilan.model_adi}")

    try:
        sonuclar = toplu_analiz(ilanlar, ilerleme=ilerleme, demo_modu=demo, effort=ayar["effort"])
    finally:
        cubuk.empty()
    return sonuclar


# ---------------------------------------------------------------------------
# Sonuç gösterimi
# ---------------------------------------------------------------------------

def _madde_listesi(maddeler: list[str], bos_mesaj: str) -> None:
    if not maddeler:
        st.caption(bos_mesaj)
        return
    st.markdown("\n".join(f"- {m}" for m in maddeler))


def ilan_karti(sonuc: AnalizSonucu) -> None:
    ilan, analiz = sonuc.ilan, sonuc.analiz
    with st.container(border=True):
        # --- Üst kısım: resim + temel bilgiler ---
        if ilan.resim_url:
            st.image(ilan.resim_url, width="stretch")
        st.subheader(ilan.model_adi)
        st.caption(ilan.baslik)

        m1, m2, m3 = st.columns(3)
        for kolon, etiket, deger in (
            (m1, "Fiyat", tl_formatla(ilan.fiyat)),
            (m2, "Yıl", ilan.yil or "—"),
            (m3, "KM", km_formatla(ilan.km)),
        ):
            kolon.caption(etiket)
            kolon.markdown(f"**{deger}**")

        with st.container(horizontal=True):
            st.badge(ilan.hasar.ozet, icon="🛡️", color=HASAR_RENK.get(ilan.hasar.durum, "gray"))
            if ilan.kasa_tipi:
                st.badge(ilan.kasa_tipi, color="blue")
            if analiz:
                if analiz.alinir_mi:
                    st.badge("Son karar: ALINIR", icon="✅", color="green")
                else:
                    st.badge("Son karar: ALINMAZ", icon="❌", color="red")
            if sonuc.demo:
                st.badge("Demo analiz", color="gray")

        # --- Alt kısım: AI analizi ---
        if sonuc.hata:
            st.error(f"AI analizi yapılamadı: {sonuc.hata}")
        elif analiz:
            sekmeler = st.tabs(["📋 Özet", "✅ Artıları", "⚠️ Eksileri", "🔧 Kronik Sorunlar", "💰 Parça & Bakım"])
            with sekmeler[0]:
                st.markdown(f"**Son karar:** {'✅ Alınır' if analiz.alinir_mi else '❌ Alınmaz'}")
                st.write(analiz.genel_degerlendirme)
            with sekmeler[1]:
                _madde_listesi(analiz.artilari, "Belirtilmiş bir artı yok.")
            with sekmeler[2]:
                _madde_listesi(analiz.eksileri, "Belirtilmiş bir eksi yok.")
            with sekmeler[3]:
                _madde_listesi(analiz.kronik_sorunlar, "Bilinen kronik sorun belirtilmedi.")
            with sekmeler[4]:
                st.write(analiz.parca_ve_bakim_maliyeti)

        with st.expander("İlan açıklaması ve hasar bilgisi kanıtları"):
            st.write(ilan.aciklama or "_Açıklama yok._")
            if ilan.hasar.kanitlar:
                st.caption("Hasar bilgisinin çıkarıldığı ifadeler:")
                for k in ilan.hasar.kanitlar:
                    st.code(k, language=None)
        if ilan.link:
            st.link_button("İlana git ↗", ilan.link, width="stretch",
                           disabled=ilan.kaynak == "mock", help="Demo ilanların gerçek bir sayfası yoktur."
                           if ilan.kaynak == "mock" else None)


def sonuclari_goster(sonuclar: list[AnalizSonucu], kaynak: str) -> None:
    if not sonuclar:
        return
    analizli = [s for s in sonuclar if s.analiz]
    alinir = [s for s in analizli if s.analiz.alinir_mi]
    fiyatlar = [s.ilan.fiyat for s in sonuclar if s.ilan.fiyat]

    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bulunan ilan", len(sonuclar))
    c2.metric("Alınır önerilen", len(alinir))
    c3.metric("Ortalama fiyat", tl_formatla(int(statistics.mean(fiyatlar))) if fiyatlar else "—")
    c4.metric("Veri kaynağı", "Demo" if kaynak == "mock" else "sahibinden.com")
    hatali = len(sonuclar) - len(analizli)
    if hatali:
        st.warning(f"{hatali} ilan analiz edilemedi; ayrıntılar kartlarda.")

    f1, f2 = st.columns([2, 1])
    siralama = f1.selectbox("Sıralama", ["Önce alınır olanlar", "Fiyat (artan)", "Kilometre (artan)", "Yıl (yeni → eski)"])
    sadece_alinir = f2.toggle("Sadece 'alınır' olanlar", value=False)

    gosterilecek = [s for s in sonuclar if not sadece_alinir or (s.analiz and s.analiz.alinir_mi)]
    anahtarlar = {
        "Önce alınır olanlar": lambda s: (not (s.analiz and s.analiz.alinir_mi), s.ilan.fiyat or 0),
        "Fiyat (artan)": lambda s: s.ilan.fiyat or 0,
        "Kilometre (artan)": lambda s: s.ilan.km if s.ilan.km is not None else 10**9,
        "Yıl (yeni → eski)": lambda s: -(s.ilan.yil or 0),
    }
    gosterilecek.sort(key=anahtarlar[siralama])

    if not gosterilecek:
        st.info("Filtreye uyan ilan yok.")
        return
    kolonlar = st.columns(2)
    for i, sonuc in enumerate(gosterilecek):
        with kolonlar[i % 2]:
            ilan_karti(sonuc)


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("🚗 Fiyat-Performans Araç Öneri ve Analiz")
    st.write("Bütçenize ve araç tipinize uygun ilanları bulur, her birini yapay zeka ile değerlendirir: "
             "artılar, eksiler, kronik sorunlar, parça maliyeti ve **alınır / alınmaz** kararı.")

    ayar = kenar_cubugu()
    kriter = arama_formu()

    if kriter is not None:
        try:
            ilanlar, kaynak = ilanlari_cek(kriter, ayar)
        except Exception as e:  # beklenmeyen hatalarda arayüz çökmesin
            logging.exception("İlan çekme hatası")
            st.error(f"İlanlar alınırken beklenmeyen bir hata oluştu: {e}")
            ilanlar, kaynak = [], ayar["kaynak"]

        if not ilanlar:
            st.info("Kriterlere uygun ilan bulunamadı. Bütçeyi artırmayı veya kasa tipini 'Farketmez' yapmayı deneyin.")
            st.session_state.pop("sonuclar", None)
        else:
            try:
                st.session_state["sonuclar"] = analiz_et(ilanlar, ayar)
                st.session_state["kaynak"] = kaynak
            except AnalizHatasi as e:
                st.error(str(e))
                st.session_state.pop("sonuclar", None)
            except Exception as e:
                logging.exception("Analiz hatası")
                st.error(f"Analiz sırasında beklenmeyen bir hata oluştu: {e}")
                st.session_state.pop("sonuclar", None)

    if "sonuclar" in st.session_state:
        sonuclari_goster(st.session_state["sonuclar"], st.session_state.get("kaynak", "mock"))


main()
