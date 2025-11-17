QBO Backend — Production ZIP
============================

This package is a production-ready Flask backend for QuickBooks Online.
It supports OAuth2, token storage in Postgres (with file fallback), automatic refresh,
and exposes endpoints:

- GET /receipts          -> returns up to 50 most recent SalesReceipts (normalized)
- GET /receipt/<id>      -> returns a single normalized receipt
- GET /connect           -> initiate QBO OAuth
- GET /callback          -> OAuth callback (saves tokens)

Included files:
- app_prod.py
- models.py
- migrate.sql
- migrate.py
- requirements.txt
- Procfile
- runtime.txt (forces Python 3.11 on Render)
- README.md

Deployment on Render (quick):
1. Create a GitHub repo and push these files.
2. Create a Render PostgreSQL instance; copy the DATABASE_URL.
3. Create a new Web Service on Render connected to your repo.
   - Build command: pip install -r requirements.txt
   - Start command: gunicorn app_prod:app --bind 0.0.0.0:$PORT --workers 3
4. Add environment variables in Render:
   - QBO_CLIENT_ID
   - QBO_CLIENT_SECRET
   - QBO_REDIRECT_URI (https://<your-backend>.onrender.com/callback)
   - FRONTEND_URL (https://<your-frontend>.vercel.app)
   - RECEIPTS_API_KEY (strong random string)
   - DATABASE_URL (from Render Postgres)
   - TOKEN_FILE (optional)
5. Run migration once: open Render Shell and run:
   python migrate.py
6. Visit https://<your-backend>.onrender.com/connect and complete OAuth.

Notes:
- runtime.txt forces Python 3.11.7 to avoid SQLAlchemy compatibility issues with Python 3.13.
- Tokens are stored in Postgres; a file fallback exists for local dev.
- The /receipts endpoint requires the x-api-key header with your RECEIPTS_API_KEY.
