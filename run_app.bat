@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -q requests beautifulsoup4 pillow
start "" pyw saak_app.py
