# TruckOS v0.2

Ny prototype med brugerlogin, separate data pr. bruger, lastbiler, servicehistorik og AI-klar diagnose.

## Start på Windows
1. Installer Python 3.11+.
2. Åbn PowerShell i mappen.
3. Kør:
   `python -m venv .venv`
   `.venv\Scripts\activate`
   `pip install -r requirements.txt`
   `python app.py`
4. Åbn `http://127.0.0.1:5000`.

## Rigtig AI
Uden AI-indstillinger bruger TruckOS en lokal fallback. For rigtig AI skal serveren have `OPENAI_API_KEY` og `OPENAI_MODEL` som miljøvariabler. Læg aldrig API-nøglen i HTML eller JavaScript.

## Online
Projektet er klar til almindelig Python-hosting. Produktionskommando: `gunicorn app:app`.

SQLite er fint til test. Før mange kunder bør databasen flyttes til en administreret database med backups og ordentlig sikkerheds-/persondatagennemgang.
