@echo off
REM Lancé par le Planificateur de tâches Windows (un passage par exécution).
REM Adapte le chemin si besoin.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python clim_watch.py >> clim-watch.log 2>&1
