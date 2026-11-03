@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] Installing requirements...
py -m pip install --upgrade -r requirements.txt || python -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto err
echo [2/3] Building EXE...
py -m PyInstaller --noconfirm --clean --onefile --windowed --name SAAK_Image_Downloader --icon app.ico --add-data "app.ico;." saak_app.py || python -m PyInstaller --noconfirm --clean --onefile --windowed --name SAAK_Image_Downloader --icon app.ico --add-data "app.ico;." saak_app.py
if errorlevel 1 goto err
echo [3/3] Done: dist\SAAK_Image_Downloader.exe
start "" "dist"
pause
exit /b 0
:err
echo BUILD FAILED
pause
exit /b 1
