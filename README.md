# Waah API (Phase‑1)

**Offline-first:** Same schema runs on SQLite (device) and Postgres (server). Use UUIDs. Sync via append-only ledger (`/sync/push` and `/sync/pull`).

## Quick start (dev)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs

## Notes
- For production, disable auto-create in `app/db.py` and run Alembic migrations.
- Add authorization to `/docs` if exposing publicly.
- Extend `orders.py` to support partial payments, split bills, and GST splits per line (fields already present).
- Add printer webhook in Phase‑2.
