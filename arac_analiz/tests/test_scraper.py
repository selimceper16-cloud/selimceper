import pytest

from scraper import (
    AramaKriterleri,
    arama_url_olustur,
    hasar_bilgisi_cikar,
    mock_ilanlar_uret,
    sayi_ayikla,
    _satirdan_ilan,
)


@pytest.mark.parametrize(
    "metin, durum, tutar",
    [
        ("Tramer kaydı yoktur, hatasız boyasız.", "Hasar kaydı yok", None),
        ("TRAMERSİZ değişensiz araç", "Hasar kaydı yok", None),
        ("Hasar kaydı: Yok", "Hasar kaydı yok", None),
        ("12.500 TL tramer kaydı mevcut, 2 parça boyalı", "Hasar kaydı var", 12500),
        ("Tramer kaydı: 8.750 TL. Lokal boya var.", "Hasar kaydı var", 8750),
        ("tramer 45 bin, motor sorunsuz", "Hasar kaydı var", 45000),
        ("35k tramer var", "Hasar kaydı var", 35000),
        ("12,5 bin TL'lik tramer var", "Hasar kaydı var", 12500),
        ("hasar kaydı var (27.300 TL), 1 parça değişen", "Hasar kaydı var", 27300),
        ("Tramer kaydı var, detay için arayın", "Hasar kaydı var", None),
        ("Ağır hasar kayıtlıdır, bilgilerinize.", "Ağır hasar kayıtlı", None),
        ("açıklama\nAğır Hasar Kayıtlı: Evet", "Ağır hasar kayıtlı", None),
        ("Ağır hasar kaydı yoktur.", "Belirtilmemiş", None),
        ("Tramer yok.\nAğır Hasar Kayıtlı: Hayır", "Hasar kaydı yok", None),
        ("2016 model, 145.000 km, acil satılık", "Belirtilmemiş", None),
        ("", "Belirtilmemiş", None),
        (None, "Belirtilmemiş", None),
    ],
)
def test_hasar_bilgisi_cikar(metin, durum, tutar):
    bilgi = hasar_bilgisi_cikar(metin)
    assert bilgi.durum == durum
    assert bilgi.tramer_tutari == tutar


def test_boya_degisen_ayiklanir():
    bilgi = hasar_bilgisi_cikar("Hatasız boyasız, tramer yok")
    assert bilgi.boya_degisen == ["Hatasız"]
    bilgi = hasar_bilgisi_cikar("2 parça boyalı, 1 parça değişen var")
    assert "2 parça boyalı" in bilgi.boya_degisen
    assert "1 parça değişen" in bilgi.boya_degisen


def test_sayi_ayikla():
    assert sayi_ayikla("1.250.000 TL") == 1_250_000
    assert sayi_ayikla("145.000") == 145_000
    assert sayi_ayikla("") is None
    assert sayi_ayikla(None) is None


@pytest.mark.parametrize("tur", ["Binek", "SUV", "Ticari"])
def test_mock_ilanlar_butceye_uyar(tur):
    kriter = AramaKriterleri(max_butce=1_500_000, arac_turu=tur)
    ilanlar = mock_ilanlar_uret(kriter, adet=10, seed=42)
    assert ilanlar, "mock üretici boş liste döndürmemeli"
    for ilan in ilanlar:
        assert ilan.fiyat <= 1_500_000
        assert ilan.yil and ilan.km and ilan.aciklama and ilan.link and ilan.resim_url
        assert ilan.hasar.durum in {"Hasar kaydı yok", "Hasar kaydı var", "Ağır hasar kayıtlı", "Belirtilmemiş"}


def test_mock_kasa_tipi_filtresi():
    kriter = AramaKriterleri(max_butce=3_000_000, arac_turu="Binek", kasa_tipi="Station Wagon")
    ilanlar = mock_ilanlar_uret(kriter, adet=8, seed=1)
    assert ilanlar
    assert all(i.kasa_tipi == "Station Wagon" for i in ilanlar)


def test_mock_cok_dusuk_butce_bos_doner():
    assert mock_ilanlar_uret(AramaKriterleri(max_butce=10_000), adet=5, seed=1) == []


def test_kriter_dogrulama():
    with pytest.raises(ValueError):
        AramaKriterleri(max_butce=0)
    with pytest.raises(ValueError):
        AramaKriterleri(max_butce=1000, arac_turu="Motosiklet")


def test_arama_url_olustur():
    k = AramaKriterleri(max_butce=900_000, arac_turu="SUV")
    assert arama_url_olustur(k) == "https://www.sahibinden.com/arazi-suv-pickup?price_max=900000&pagingSize=20"
    assert "pagingOffset=20" in arama_url_olustur(k, sayfa=2)


def test_satirdan_ilan():
    ilan = _satirdan_ilan({
        "baslik": "Temiz Golf",
        "href": "/ilan/vasita-otomobil-volkswagen-temiz-golf-123/detay",
        "resim": "https://img/x.jpg",
        "etiketler": ["Golf", "1.2 TSI Comfortline"],
        "ozellikler": ["2016", "145.000", "Beyaz"],
        "fiyat": "1.150.000 TL",
    })
    assert ilan.fiyat == 1_150_000 and ilan.yil == 2016 and ilan.km == 145_000
    assert ilan.link.startswith("https://www.sahibinden.com/ilan/")
    assert ilan.seri == "Golf"
