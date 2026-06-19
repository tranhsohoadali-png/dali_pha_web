@echo off
rem Chay DALI Print Agent o che do NEN (khong cua so). Tu tim pythonw.
cd /d "%~dp0"
where pythonw >nul 2>nul && (
  start "" pythonw "%~dp0dali_print_agent.py"
  exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" (
  start "" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" "%~dp0dali_print_agent.py"
  exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" (
  start "" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" "%~dp0dali_print_agent.py"
  exit /b
)
start "" pythonw "%~dp0dali_print_agent.py"
