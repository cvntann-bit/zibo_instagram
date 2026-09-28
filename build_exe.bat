@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Zibo Bot - EXE olusturuluyor
echo.
echo  === Zibo Bot EXE olusturucu ===
echo.

rem --- Python var mi? ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python bulunamadi, kuruluyor...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo.
    echo Python kuruldu. Bu pencereyi kapatip build_exe.bat dosyasina TEKRAR cift tikla.
    pause
    exit /b
)

echo [1/3] Gerekli paketler kuruluyor...
%PY% -m pip install --upgrade pip >nul
%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto hata

echo [2/3] EXE olusturuluyor (1-3 dakika surebilir)...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name ZiboBot ^
  --hidden-import captioner --hidden-import images --hidden-import instagram --hidden-import tiktok ^
  --collect-data certifi gui.py
if errorlevel 1 goto hata

echo [3/3] Uygulama klasoru hazirlaniyor...
taskkill /f /im ZiboBot.exe >nul 2>&1
timeout /t 2 /nobreak >nul
if not exist "ZiboBot_Uygulama" mkdir "ZiboBot_Uygulama"
copy /y "dist\ZiboBot.exe" "ZiboBot_Uygulama\ZiboBot.exe" >nul
if errorlevel 1 (
    echo  ZiboBot.exe kopyalanamadi. Uygulama aciksa kapatip bu dosyayi tekrar calistir.
    pause
    exit /b
)
rmdir /s /q build >nul 2>&1
del /q ZiboBot.spec >nul 2>&1

echo.
echo  TAMAM! "ZiboBot_Uygulama" klasorundeki ZiboBot.exe'ye cift tikla.
echo  Ilk acilista Ayarlar'dan anahtarlarini gir.
echo  Pencere basliginda "Zibo Bot v1.4" yaziyorsa yeni surumdesin.
echo.
explorer "ZiboBot_Uygulama"
pause
exit /b

:hata
echo.
echo  Bir hata olustu. Yukaridaki kirmizi satirlarin ekran goruntusunu Claude'a gonder.
pause
