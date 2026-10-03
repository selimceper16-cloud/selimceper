"""
AI analiz modülü testleri. Gerçek API'ye gitmez: yerel sahte bir HTTP sunucusu Claude API'yi taklit eder,
böylece SDK'nın gönderdiği istek gövdesi ve yanıtın Pydantic modeline ayrıştırılması doğrulanır.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic
import pytest

import ai_analyzer
from ai_analyzer import AnalizHatasi, AracAnalizi, arac_analiz_et, demo_analiz, prompt_olustur, toplu_analiz
from scraper import AramaKriterleri, mock_ilanlar_uret

ORNEK_ANALIZ = {
    "alinir_mi": True,
    "genel_degerlendirme": "Fiyatı makul görünüyor.",
    "artilari": ["Yakıt cimrisi"],
    "eksileri": ["Yalıtım zayıf"],
    "kronik_sorunlar": ["DSG mekatronik"],
    "parca_ve_bakim_maliyeti": "Ortalama: parçası yaygın.",
}


class _SahteClaude(BaseHTTPRequestHandler):
    istekler: list = []
    durum_kodu = 200
    stop_reason = "end_turn"

    def do_POST(self):  # noqa: N802
        govde = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).istekler.append({"yol": self.path, "govde": govde, "beta": self.headers.get("anthropic-beta")})
        if self.durum_kodu != 200:
            yanit = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        else:
            yanit = {
                "id": "msg_test", "type": "message", "role": "assistant", "model": govde["model"],
                "content": [{"type": "text", "text": json.dumps(ORNEK_ANALIZ, ensure_ascii=False)}],
                "stop_reason": self.stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }
        veri = json.dumps(yanit).encode()
        self.send_response(self.durum_kodu)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(veri)))
        self.end_headers()
        self.wfile.write(veri)

    def log_message(self, *args):
        pass


@pytest.fixture
def sahte_sunucu():
    _SahteClaude.istekler = []
    _SahteClaude.durum_kodu = 200
    _SahteClaude.stop_reason = "end_turn"
    sunucu = HTTPServer(("127.0.0.1", 0), _SahteClaude)
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()
    yield sunucu
    sunucu.shutdown()


def _istemci(sunucu):
    return anthropic.Anthropic(api_key="test", base_url=f"http://127.0.0.1:{sunucu.server_port}", max_retries=0)


@pytest.fixture
def ilan():
    return mock_ilanlar_uret(AramaKriterleri(max_butce=1_500_000), adet=1, seed=3)[0]


def test_arac_analiz_et_yapilandirilmis_yanit(sahte_sunucu, ilan):
    sonuc = arac_analiz_et(ilan, istemci=_istemci(sahte_sunucu), model="claude-opus-5-5", effort="medium")
    assert isinstance(sonuc, AracAnalizi)
    assert sonuc.alinir_mi is True and sonuc.kronik_sorunlar == ["DSG mekatronik"]

    istek = _SahteClaude.istekler[0]
    govde = istek["govde"]
    assert istek["yol"].startswith("/v1/messages")
    assert govde["model"] == "claude-opus-5-5"
    assert govde["output_config"]["effort"] == "medium"
    assert govde["output_config"]["format"]["type"] == "json_schema"
    assert set(govde["output_config"]["format"]["schema"]["required"]) == set(ORNEK_ANALIZ)
    assert govde["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in istek["beta"]
    assert ilan.model_adi in govde["messages"][0]["content"]


def test_gecersiz_anahtar_anlasilir_hata(sahte_sunucu, ilan):
    _SahteClaude.durum_kodu = 401
    with pytest.raises(AnalizHatasi, match="API anahtarı geçersiz"):
        arac_analiz_et(ilan, istemci=_istemci(sahte_sunucu))


def test_reddetme_durumu(sahte_sunucu, ilan):
    _SahteClaude.stop_reason = "refusal"
    with pytest.raises(AnalizHatasi, match="reddetti"):
        arac_analiz_et(ilan, istemci=_istemci(sahte_sunucu))


def test_toplu_analiz_sira_ve_hata_izolasyonu(monkeypatch):
    ilanlar = mock_ilanlar_uret(AramaKriterleri(max_butce=2_000_000), adet=5, seed=5)
    monkeypatch.setattr(ai_analyzer, "istemci_olustur", lambda: object())

    def sahte_analiz(ilan, istemci, model=None, effort=None):
        if ilan is ilanlar[2]:
            raise AnalizHatasi("kota doldu")
        return AracAnalizi(**ORNEK_ANALIZ)

    monkeypatch.setattr(ai_analyzer, "arac_analiz_et", sahte_analiz)
    cagrilar = []
    sonuclar = toplu_analiz(ilanlar, max_paralel=3, ilerleme=lambda i, n, s: cagrilar.append((i, n)))
    assert [s.ilan for s in sonuclar] == ilanlar
    assert sonuclar[2].hata == "kota doldu" and sonuclar[2].analiz is None
    assert all(s.analiz for i, s in enumerate(sonuclar) if i != 2)
    assert len(cagrilar) == 5 and cagrilar[-1] == (5, 5)


def test_anahtar_yoksa_hata(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnalizHatasi, match="ANTHROPIC_API_KEY"):
        ai_analyzer.istemci_olustur()


def test_demo_analiz_ve_prompt(ilan):
    assert isinstance(demo_analiz(ilan), AracAnalizi)
    sonuclar = toplu_analiz([ilan], demo_modu=True)
    assert sonuclar[0].demo and sonuclar[0].analiz
    p = prompt_olustur(ilan)
    assert ilan.hasar.ozet in p and "Fiyat:" in p
