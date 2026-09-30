@echo off
chcp 65001 >nul
rem Сборка Kassa.exe на своём компьютере (нужен Python 3.12 с python.org).
rem Перед сборкой замените robinhilk488-bot/Lotus в main.py на свой репозиторий GitHub.
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :error
python -m PyInstaller --noconfirm --noconsole --onefile --name Kassa --icon kassa.ico --add-data "ui;ui" main.py || goto :error
echo.
echo Готово: dist\Kassa.exe
pause
exit /b 0
:error
echo Сборка не удалась, смотрите сообщения выше.
pause
exit /b 1
