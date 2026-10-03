# 💡 TÜBİTAK / Teknofest İnovasyon Zekası

Seçilen **eğitim seviyesi** ve **yarışma kategorisine** göre, geçmiş projeleri (yerel RAG) ve
internetteki güncel teknoloji trendlerini harmanlayıp **daha önce yapılmamış, prototiplenebilir**
proje fikirleri üreten masaüstü uygulaması. LLM ve vektör veritabanı **tamamen bilgisayarınızda** çalışır.

![Ekran görüntüsü](docs/ekran_goruntusu.png)

## Mimari

| Dosya | Görev |
|---|---|
| `main.py` | CustomTkinter koyu temalı arayüz (arka plan thread'i + canlı akış) |
| `ai_engine.py` | LangChain + Ollama; RAG ve web bağlamını sistem promptuna gömer |
| `search_module.py` | DuckDuckGo (API anahtarsız) ile son 1 yılın trendlerini toplar ve özetler |
| `database_builder.py` | `gecmis_projeler/` içindeki PDF/TXT/MD/DOCX dosyalarını parçalayıp ChromaDB'ye yazar |
| `config.py` | Model adları, yollar, kategoriler ve limitler (tek yerden ayar) |
| `selftest.py` | Paketlenmiş exe'nin bağımlılık doğrulaması (`--selftest`) |
| `packaging/` | PyInstaller (`.spec`), Inno Setup (`installer.iss`) ve ikon |

Akış: **ChromaDB'den geçmiş projeler (Context 1)** → **DuckDuckGo'dan güncel haberler (Context 2)** → **Ollama**.

## Windows'a Kurulum (Setup.exe) — önerilen

Terminal veya Python gerekmez.

1. [Ollama for Windows](https://ollama.com/download/windows)'u kurun.
2. Kurulum dosyasını indirin:
   - **Sürümler:** depo sayfasındaki **Releases** bölümünden `InovasyonZekasi-Kurulum-x.y.z.exe`
   - **veya en son derleme:** **Actions** sekmesi → *Windows Kurulum Dosyası* → en üstteki yeşil çalıştırma →
     sayfanın altındaki **Artifacts** bölümünden zip'i indirip açın.
3. `InovasyonZekasi-Kurulum-x.y.z.exe`'yi çalıştırın. Yönetici izni istemez.
   > İmzasız olduğu için Windows SmartScreen *"Windows bilgisayarınızı korudu"* diyebilir:
   > **Ek bilgi → Yine de çalıştır**.
4. Uygulamayı açın. Model eksikse sol alttaki **⬇ Eksik Modelleri İndir** butonuna basın (ilk seferde ~5 GB).
5. Geçmiş proje raporları için **📁 Proje Klasörünü Aç** → dosyaları kopyalayın → **🔄 Veritabanını Güncelle**.

Kurulu sürümde veriler şuralarda tutulur:
- Proje raporları: `Belgeler\InovasyonZekasi\gecmis_projeler`
- Vektör veritabanı ve hata kaydı: `%LOCALAPPDATA%\InovasyonZekasi`

### Kurulum dosyasını kendiniz üretmek

GitHub Actions her push'ta `Setup.exe` üretir; bunun için `.github/workflows/windows-installer.yml`
dosyasına bakın. `v1.2.0` gibi bir etiket push edilirse kurulum dosyası Releases sayfasında yayınlanır.
Kendi bilgisayarınızda üretmek için Python 3.12 ve [Inno Setup 6](https://jrsoftware.org/isdl.php)
kurulu olmalı. Ardından `build_windows.bat` dosyasına çift tıklayın.

Paketlenmiş uygulamanın bütün bağımlılıklarını doğrulamak için:
`InovasyonZekasi.exe --selftest` komutunu çalıştırın. Sonuç `%LOCALAPPDATA%\InovasyonZekasi\selftest_sonucu.txt`
dosyasına yazılır.

## Kaynak Koddan Kurulum (geliştiriciler için)

1. [Ollama](https://ollama.com)'yı kurun ve modelleri indirin:
   ```bash
   ollama pull llama3.1          # veya: ollama pull gemma2
   ollama pull nomic-embed-text  # geçmiş projeler (RAG) için embedding modeli
   ```
2. Python 3.10+ ile bağımlılıkları kurun (Linux'ta ayrıca `sudo apt install python3-tk`):
   ```bash
   python -m venv .venv
   source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Geçmiş proje raporlarını `gecmis_projeler/` klasörüne koyup veritabanını oluşturun.
   Yeni dosya ekledikçe tekrar çalıştırın; eklenmiş dosyalar atlanır. Bunu arayüzdeki
   **🔄 Veritabanını Güncelle** butonuyla da yapabilirsiniz:
   ```bash
   python database_builder.py          # --reset ile sıfırdan kurar
   ```
4. Uygulamayı başlatın:
   ```bash
   python main.py
   ```

Veritabanı boş olsa da uygulama çalışır; bu durumda model klasik projeleri kendi bilgisinden eler.

## Ayarlar

Kod değiştirmeden ortam değişkenleriyle:

```bash
INOVASYON_LLM_MODEL=gemma2 python main.py
INOVASYON_TEMPERATURE=0.9 INOVASYON_NUM_CTX=16384 python main.py
```

Kategoriler ve limitler `config.py` içindedir.

## Gizlilik

- LLM, embedding ve ChromaDB yerel çalışır; ChromaDB telemetrisi ve LangSmith izleme kodda kapatılmıştır.
- İnternete giden **tek** şey, web araması açıksa DuckDuckGo'ya gönderilen kategori sorgusudur
  (ör. *"agriculture agritech smart farming innovation breakthrough"*). Raporlarınız veya üretilen
  fikirler dışarı gönderilmez. Arayüzdeki anahtarla web araması tamamen kapatılabilir.
