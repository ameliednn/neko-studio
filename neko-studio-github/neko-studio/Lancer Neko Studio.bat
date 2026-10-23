@echo off
rem Double-cliquez sur ce fichier pour ouvrir Neko Studio dans votre navigateur.
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python est introuvable. Suivez l'etape 1 du guide d'installation.
  pause
  exit /b 1
)
python -m pip install --quiet --user -r requirements.txt
start "" http://localhost:8501
python -m streamlit run app.py
pause
