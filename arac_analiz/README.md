# 🚗 Fiyat-Performans Araç Öneri ve Analiz Uygulaması

Bütçe ve araç tipine göre sahibinden.com'dan (veya test için sahte/mock veriden) ilan toplar, her ilanın
açıklamasından **Tramer / hasar kaydı** bilgisini ayıklar ve her aracı **Claude** ile değerlendirip
artılar, eksiler, kronik sorunlar, parça/bakım maliyeti ve **alınır / alınmaz** kararıyla kartlar halinde gösterir.

## Mimari

| Dosya | Görev |
|---|---|
| `app.py` | Streamlit arayüzü: arama formu, ayarlar, ilerleme çubuğu, sonuç kartları (sekmeler + açılır menüler) |
| `scraper.py` | Playwright ile sahibinden.com scraper'ı (rastgele user-agent, rastgele beklemeler, engel tespiti), `hasar_bilgisi_cikar()` regex yardımcı fonksiyonu ve `mock_ilanlar_uret()` sahte veri üretici |
| `ai_analyzer.py` | Claude API ile analiz; yanıt `AracAnalizi` Pydantic modeline doğrulanmış JSON olarak döner. Paralel toplu analiz ve API'siz `demo_analiz()` |
| `tests/` | Hasar ayıklayıcı, mock veri, URL üretimi ve AI modülü testleri (gerçek API'ye gitmez) |

Akış: **Form** → `ilanlari_getir()` (mock veya sahibinden) → `hasar_bilgisi_cikar()` → `toplu_analiz()` (Claude) → **Kartlar**

AI yanıt şeması:

```json
{
  "alinir_mi": true,
  "genel_degerlendirme": "…",
  "artilari": ["Yakıt cimrisi", "İkinci eli hızlı"],
  "eksileri": ["Yalıtım zayıf"],
  "kronik_sorunlar": ["DSG mekatronik arızası"],
  "parca_ve_bakim_maliyeti": "Ortalama: …"
}
```

## Kurulum

```bash
cd arac_analiz
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium          # yalnızca gerçek scraper için gerekli

cp .env.example .env                 # Windows: copy .env.example .env
# .env içine ANTHROPIC_API_KEY=sk-ant-... yazın
```

## Çalıştırma

```bash
streamlit run app.py
```

Kenar çubuğundan:

- **Veri kaynağı:** `Demo veri (mock)` internet/bot koruması olmadan test etmek içindir. `sahibinden.com (gerçek)`
  seçilirse 1-2 sayfa taranır; engellenirse otomatik olarak demo veriye geçilebilir.
- **Analiz modu:** `Claude AI (API)` gerçek analiz yapar. `Demo analiz (API'siz)` API anahtarı olmadan
  arayüzü denemek için kurallarla örnek sonuç üretir (gerçek yapay zeka değerlendirmesi değildir).
- **Maksimum ilan:** Her ilan bir API çağrısıdır; maliyeti ve süreyi bu sayı belirler.

Komut satırından hızlı deneme:

```bash
python scraper.py            # mock ilanları ve çıkarılan hasar bilgisini listeler
python scraper.py gercek     # sahibinden.com'u dener
python ai_analyzer.py demo   # API'siz örnek analiz
python ai_analyzer.py        # ilk mock ilanı Claude ile analiz eder (API anahtarı gerekir)
python -m pytest -q          # testler
```

## Ayarlar (.env)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Zorunlu (Claude AI modu için) |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Kullanılacak Claude modeli |
| `CLAUDE_EFFORT` | `medium` | Analiz derinliği (`low` … `max`); arayüzdeki kaydırıcı bunu geçersiz kılar |
| `AI_MAX_PARALEL` | `4` | Aynı anda yapılan analiz sayısı |
| `PLAYWRIGHT_CHROMIUM_PATH` | — | Playwright'ın indirdiği yerine sistemdeki Chrome/Chromium'u kullanmak için |

## Önemli notlar

- **sahibinden.com bot koruması:** Site Cloudflare ve kendi doğrulama sayfalarını kullanır; headless tarayıcılar
  sık engellenir. Engellenirseniz `headless` seçeneğini kapatmayı, daha sonra denemeyi ya da demo veriyi kullanmayı
  deneyin. Sitenin HTML yapısı değişirse `scraper.py` içindeki seçicilerin (`tr.searchResultsItem`,
  `ul.classifiedInfoList`, `#classifiedDescription`) güncellenmesi gerekebilir. Siteyi yoğun taramak kullanım
  koşullarına aykırı olabilir; düşük hacimde ve kişisel amaçla kullanın.
- **Kasa tipi filtresi** sahibinden'de kategoriye özel kodlarla tutulduğundan URL'ye eklenmez; ilan detayındaki
  "Kasa Tipi" alanına göre sonradan filtrelenir (Binek araçlar için anlamlıdır).
- **Hasar bilgisi** açıklamadaki serbest metinden regex ile çıkarılır ("12.500 TL tramer", "tramersiz",
  "Ağır Hasar Kayıtlı: Evet" vb.); her zaman doğru olmayabilir. Kartlardaki "İlan açıklaması" menüsünde hangi
  ifadeden çıkarıldığı gösterilir.
- **AI fiyat yorumu** modelin genel piyasa bilgisine dayanır; canlı piyasa verisi kullanılmaz. Satın almadan önce
  mutlaka ekspertiz yaptırın.
- Claude'un güvenlik filtresi bir isteği reddederse API isteği otomatik olarak uygun bir modelle tekrarlar
  (`fallbacks="default"`); istemiyorsanız `ai_analyzer.py` içindeki `betas`/`fallbacks` satırlarını kaldırın.
