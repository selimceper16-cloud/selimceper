@echo off
REM ---------------------------------------------------------------------------
REM  Windows kurulum dosyasini (Setup.exe) kendi bilgisayarinizda uretir.
REM  Gereken: Python 3.12 (PATH'te) ve Inno Setup 6 (https://jrsoftware.org/isdl.php)
REM  Kullanim: proje klasorunde bu dosyaya cift tiklayin.
REM  Cikti   : Output\InovasyonZekasi-Kurulum-<surum>.exe
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo [x] Python bulunamadi. Python 3.12 kurup "Add python.exe to PATH" secin. & pause & exit /b 1)

if not exist .build-venv (
    echo [1/4] Derleme ortami olusturuluyor...
    python -m venv .build-venv || (pause & exit /b 1)
)
call .build-venv\Scripts\activate.bat

echo [2/4] Kutuphaneler kuruluyor...
python -m pip install --upgrade pip >nul
pip install -r packaging\requirements-build.txt || (pause & exit /b 1)

echo [3/4] Uygulama paketleniyor (PyInstaller)...
pyinstaller --noconfirm --clean packaging\InovasyonZekasi.spec || (pause & exit /b 1)

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo [x] Inno Setup 6 bulunamadi: https://jrsoftware.org/isdl.php
    echo     Kurulum olmadan da uygulama hazir: dist\InovasyonZekasi\InovasyonZekasi.exe
    pause & exit /b 1
)

for /f %%v in ('python -c "import config; print(config.APP_VERSION)"') do set APPVER=%%v
echo [4/4] Kurulum dosyasi olusturuluyor (surum %APPVER%)...
%ISCC% /DAppVersion=%APPVER% packaging\installer.iss || (pause & exit /b 1)

echo.
echo [OK] Hazir: Output\InovasyonZekasi-Kurulum-%APPVER%.exe
pause
