"""DuckDuckGo üzerinden (API anahtarsız) güncel teknoloji trendlerini toplar ve özetler.

Gizlilik notu: İnternete giden TEK bilgi arama sorgusudur (kategori anahtar
kelimeleri). Kullanıcı verisi, geçmiş proje raporları veya LLM çıktısı dışarı
gönderilmez. Arayüzden web araması tamamen kapatılabilir.

Bağımsız test:
    python search_module.py "Tarım"
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import config

try:  # Paketin yeni adı "ddgs"
    from ddgs import DDGS

    # ddgs varsayılan olarak ("auto") sorguyu Brave/Mojeek gibi başka motorlara da dağıtabilir.
    # Sorgunun YALNIZCA DuckDuckGo'ya gitmesi için backend sabitlenir.
    _BACKEND_KW = {"backend": "duckduckgo"}
except ImportError:  # Eski ad: duckduckgo-search (yalnızca DuckDuckGo kullanır)
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None  # type: ignore[assignment]
    _BACKEND_KW = {}


@dataclass
class TrendItem:
    title: str
    url: str
    snippet: str
    source: str = ""
    date: str = ""
    score: float = field(default=0.0, compare=False)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\w+", re.UNICODE)


def _build_queries(kategori: str) -> list[tuple[str, str, str]]:
    """(tür, sorgu, bölge) listesi döner."""
    en = config.KATEGORILER.get(kategori, kategori)
    year = datetime.now().year
    return [
        ("news", f"{en} innovation breakthrough", "wt-wt"),
        ("text", f"{en} new technology breakthrough {year}", "wt-wt"),
        ("text", f"{kategori} alanında yeni teknoloji inovasyon {year}", "tr-tr"),
    ]


def _run_query(ddgs, kind: str, query: str, region: str, max_results: int) -> list[TrendItem]:
    items: list[TrendItem] = []
    if kind == "news":
        # Haber araması 'y' desteklemeyebilir; en geniş güvenli aralık olarak 'm' kullanılır.
        raw = ddgs.news(query, region=region, safesearch="moderate", timelimit="m",
                        max_results=max_results, **_BACKEND_KW) or []
        for r in raw:
            items.append(TrendItem(
                title=r.get("title", ""), url=r.get("url") or r.get("href", ""),
                snippet=r.get("body", ""), source=r.get("source", ""),
                date=(r.get("date") or "")[:10],
            ))
    else:
        # Son 1 yıl ile sınırlı genel web araması
        raw = ddgs.text(query, region=region, safesearch="moderate", timelimit="y",
                        max_results=max_results, **_BACKEND_KW) or []
        for r in raw:
            items.append(TrendItem(
                title=r.get("title", ""), url=r.get("href") or r.get("url", ""),
                snippet=r.get("body", ""),
            ))
    return items


def _score(item: TrendItem, keywords: set[str]) -> float:
    words = {w.lower() for w in _WORD.findall(f"{item.title} {item.snippet}")}
    overlap = len(words & keywords)
    novelty = sum(1 for w in ("new", "first", "novel", "breakthrough", "yeni", "ilk", "geliştirdi",
                              "researchers", "araştırmacılar", "prototype", "startup") if w in words)
    recency = 1.5 if item.date else 0.0
    return overlap * 2 + novelty + recency + min(len(item.snippet), 300) / 300


def _shorten(text: str, max_sentences: int = 2, max_chars: int = 320) -> str:
    text = " ".join(text.split())
    sentences = _SENTENCE_SPLIT.split(text)
    out = " ".join(sentences[:max_sentences])
    return out if len(out) <= max_chars else out[: max_chars - 1].rsplit(" ", 1)[0] + "…"


def summarize(items: list[TrendItem], keywords: set[str], max_items: int,
              max_chars: int) -> tuple[str, list[TrendItem]]:
    """Sonuçları tekilleştirir, puanlar ve LLM'e verilecek kompakt madde listesi üretir."""
    seen: set[str] = set()
    unique: list[TrendItem] = []
    for it in items:
        key = (it.url or it.title).lower().rstrip("/")
        norm_title = re.sub(r"\W+", "", it.title.lower())[:60]
        if not it.snippet or key in seen or norm_title in seen:
            continue
        seen.update({key, norm_title})
        it.score = _score(it, keywords)
        unique.append(it)

    unique.sort(key=lambda x: x.score, reverse=True)
    lines: list[str] = []
    used: list[TrendItem] = []
    total = 0
    for i, it in enumerate(unique[:max_items], 1):
        meta = ", ".join(x for x in (it.source, it.date) if x)
        line = f"{i}. {it.title}{f' ({meta})' if meta else ''}: {_shorten(it.snippet)}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        used.append(it)
        total += len(line) + 1
    return "\n".join(lines), used


def search_trends(
    kategori: str,
    max_results: int = config.WEB_MAX_RESULTS,
    max_chars: int = config.WEB_MAX_CHARS,
) -> tuple[str, list[TrendItem]]:
    """Kategoriyle ilgili son 1 yıldaki teknolojik gelişmeleri arar ve özetler.

    Dönüş: (LLM'e verilecek özet metin, kullanılan sonuçların listesi)
    İnternet yoksa veya arama başarısız olursa ("", []) döner; uygulama çökmez.
    """
    if DDGS is None:
        raise RuntimeError("Web arama kütüphanesi yüklü değil: pip install ddgs")

    keywords = {w.lower() for w in _WORD.findall(f"{kategori} {config.KATEGORILER.get(kategori, '')}")}
    collected: list[TrendItem] = []
    errors: list[str] = []

    with DDGS() as ddgs:
        for n, (kind, query, region) in enumerate(_build_queries(kategori)):
            if n:
                time.sleep(0.8)  # DuckDuckGo hız sınırına takılmamak için
            try:
                collected.extend(_run_query(ddgs, kind, query, region, max_results))
            except Exception as exc:  # noqa: BLE001 - tek sorgu düşerse diğerleriyle devam
                errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")

    if not collected and errors:
        raise RuntimeError("İnternete/DuckDuckGo'ya ulaşılamadı (" + errors[0] + ")")

    return summarize(collected, keywords, max_items=max_results, max_chars=max_chars)


if __name__ == "__main__":
    import sys

    cat = sys.argv[1] if len(sys.argv) > 1 else "Yapay Zeka"
    text, results = search_trends(cat)
    print(text or "(sonuç yok)")
    print("\nKaynaklar:")
    for r in results:
        print(" -", r.url)
