@echo off
chcp 65001 >nul
rem Нужен Python 3.9 (cefpython3 работает только на 3.9).
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :error
python -m PyInstaller --noconfirm --noconsole --onefile --name Lotus --icon kassa.ico --add-data "ui;ui" --collect-all webview --collect-all cefpython3 main.py || goto :error
echo.
echo Готово: dist\Lotus.exe
pause
exit /b 0
:error
echo Сборка не удалась, смотрите сообщения выше.
pause
exit /b 1
