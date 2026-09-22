@echo off
REM DALI Bambu Agent - chay tren PC xuong (cung LAN voi may in A1)
cd /d "%~dp0"
where python >nul 2>nul || (echo Chua cai Python 3. Tai tai python.org roi tich "Add to PATH". & pause & exit /b)
python -m pip install -r requirements.txt
echo.
echo === DALI Bambu Agent dang chay. Dong cua so nay de tat. ===
python bambu_agent.py
pause
