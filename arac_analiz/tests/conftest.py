import sys
from pathlib import Path

# Testlerin proje kökündeki modülleri (scraper, ai_analyzer) bulabilmesi için
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
