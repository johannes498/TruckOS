# TruckOS v0.4

TruckOS v0.4 bygger videre på v0.3 og tilføjer:

- PostgreSQL via `DATABASE_URL` med SQLite fallback lokalt
- Rediger og slet lastbiler
- Rediger og slet serviceposter
- Forbedret servicehistorik med lastbilnavn og nummerplade
- Profil med navn og firma
- Nyt dashboard og mobilvenligt design
- Diagnosehistorik
- Rigtig OpenAI-baseret AI Diagnose via Responses API
- Lokal sikker fallback hvis OpenAI ikke er sat op
- CSRF-beskyttelse på ændringer
- `/health` endpoint til Render health checks

## Render

Build command:

`pip install -r requirements.txt`

Start command:

`gunicorn app:app`

Environment variables:

- `DATABASE_URL` = Render Postgres Internal Database URL
- `SECRET_KEY` = lang tilfældig hemmelig værdi
- `OPENAI_API_KEY` = OpenAI API key
- `OPENAI_MODEL` = `gpt-5.6-luna` (kan ændres)

Gem aldrig API keys eller database-URLs i GitHub.
