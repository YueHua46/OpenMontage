@echo off
rem Update code, skill and team relay config from your main branch. Keys in .env are not touched.
cd /d "%~dp0"
git pull --ff-only origin main
if errorlevel 1 (
  echo Update failed. Nothing was overwritten.
  if not "%~1"=="--no-pause" pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
.venv\Scripts\python.exe -X utf8 .agents\skills\openmontage\scripts\doctor.py --repo .
if not "%~1"=="--no-pause" pause
