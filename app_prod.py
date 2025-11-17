import os
import json
import datetime
import logging
from urllib.parse import urlencode
from flask import Flask, request, redirect, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import requests

# SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from models import Base, Token

# Load env (for local dev)
load_dotenv()

# Configuration from env
QBO_CLIENT_ID = os.getenv("QBO_CLIENT_ID")
QBO_CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")
QBO_REALM_ID = os.getenv("QBO_REALM_ID")
QBO_REDIRECT_URI = os.getenv("QBO_REDIRECT_URI")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://<frontend>.vercel.app")
RECEIPTS_API_KEY = os.getenv("RECEIPTS_API_KEY", "devkey")
DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN_FILE = os.getenv("TOKEN_FILE", "tokens.json")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# QuickBooks endpoints
QB_AUTH = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_BASE = "https://quickbooks.api.intuit.com"

# Logging
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("qbo-backend")

# DB setup
engine = None
Session = None
if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL, future=True)
        Session = sessionmaker(bind=engine)
        # Ensure tables exist (declarative Base metadata)
        Base.metadata.create_all(engine)
        logger.info("Connected to database and ensured tables exist.")
    except Exception as e:
        logger.exception("Failed to connect to database: %s", e)
else:
    logger.warning("No DATABASE_URL set - using file fallback for tokens.")

app = Flask(__name__)
# Restrict CORS to FRONTEND_URL only
CORS(app, origins=[FRONTEND_URL], supports_credentials=False)

# ---------------- Token storage helpers ----------------
def save_tokens_db(token_resp, realm_id=None):
    if not Session:
        return False
    s = Session()
    try:
        # Delete previous tokens (keep single row)
        s.query(Token).delete()
        t = Token(
            realm_id=realm_id,
            access_token=token_resp.get("access_token"),
            refresh_token=token_resp.get("refresh_token"),
            token_type=token_resp.get("token_type"),
            expires_at=(datetime.datetime.utcnow() + datetime.timedelta(seconds=int(token_resp.get("expires_in", 3600)))),
            raw=token_resp
        )
        s.add(t)
        s.commit()
        logger.info("Saved tokens to database for realm %s", realm_id)
        return True
    except Exception:
        logger.exception("Failed to save tokens to DB")
        return False
    finally:
        s.close()

def load_tokens_db():
    if not Session:
        return None
    s = Session()
    try:
        obj = s.query(Token).order_by(Token.id.desc()).first()
        if not obj:
            return None
        return {
            "access_token": obj.access_token,
            "refresh_token": obj.refresh_token,
            "token_type": obj.token_type,
            "expires_at": obj.expires_at,
            "realm_id": obj.realm_id,
            "raw": obj.raw
        }
    except Exception:
        logger.exception("Failed to load tokens from DB")
        return None
    finally:
        s.close()

def save_tokens_file(token_resp, realm_id=None):
    payload = token_resp.copy()
    payload['_realm_id'] = realm_id
    try:
        with open(TOKEN_FILE, 'w') as f:
            json.dump(payload, f, default=str)
        logger.info("Saved tokens to file fallback %s", TOKEN_FILE)
        return True
    except Exception:
        logger.exception("Failed to write tokens file")
        return False

def load_tokens_file():
    try:
        with open(TOKEN_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return None

def get_tokens():
    # prefer DB
    t = None
    if Session:
        t = load_tokens_db()
    if not t:
        t = load_tokens_file()
    return t

# Refresh tokens if expired or near expiry
def refresh_tokens_if_needed():
    t = get_tokens()
    if not t:
        logger.debug("No tokens available to refresh.")
        return None
    expires_at = t.get('expires_at')
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.datetime.fromisoformat(expires_at)
        except Exception:
            expires_at = None
    if expires_at and expires_at > datetime.datetime.utcnow() + datetime.timedelta(seconds=60):
        return t
    refresh_token = t.get('refresh_token') or (t.get('raw') or {}).get('refresh_token')
    if not refresh_token:
        logger.warning("No refresh token available.")
        return None
    auth = (QBO_CLIENT_ID, QBO_CLIENT_SECRET)
    headers = {'Accept':'application/json', 'Content-Type':'application/x-www-form-urlencoded'}
    data = {'grant_type':'refresh_token', 'refresh_token': refresh_token}
    try:
        res = requests.post(QB_TOKEN, data=urlencode(data), headers=headers, auth=auth, timeout=15)
        if res.status_code != 200:
            logger.error('Failed to refresh token: %s %s', res.status_code, res.text)
            return None
        token_resp = res.json()
        realm = t.get('realm_id') or (t.get('raw') or {}).get('realmId') or QBO_REALM_ID
        if Session:
            save_tokens_db(token_resp, realm)
        else:
            save_tokens_file(token_resp, realm)
        logger.info("Refreshed tokens successfully.")
        return get_tokens()
    except Exception:
        logger.exception("Exception during token refresh")
        return None

# ---------------- OAuth endpoints ----------------
@app.route('/connect')
def connect():
    if not QBO_CLIENT_ID or not QBO_REDIRECT_URI:
        return ("Missing QBO_CLIENT_ID or QBO_REDIRECT_URI", 500)
    scope = 'com.intuit.quickbooks.accounting openid profile email phone address'
    params = {
        'client_id': QBO_CLIENT_ID,
        'response_type': 'code',
        'scope': scope,
        'redirect_uri': QBO_REDIRECT_URI,
        'state': 'qbo_state_1'
    }
    url = f'{QB_AUTH}?{urlencode(params)}'
    return redirect(url)

@app.route('/callback')
def callback():
    error = request.args.get('error')
    if error:
        return (f'OAuth error: {error}', 400)
    code = request.args.get('code')
    realm = request.args.get('realmId') or QBO_REALM_ID
    if not code:
        return ('Missing code', 400)
    auth = (QBO_CLIENT_ID, QBO_CLIENT_SECRET)
    headers = {'Accept':'application/json', 'Content-Type':'application/x-www-form-urlencoded'}
    data = {'grant_type':'authorization_code','code':code,'redirect_uri':QBO_REDIRECT_URI}
    try:
        res = requests.post(QB_TOKEN, data=urlencode(data), headers=headers, auth=auth, timeout=15)
        if res.status_code != 200:
            logger.error('Token exchange failed: %s %s', res.status_code, res.text)
            return (f'Token exchange failed: {res.status_code}', 500)
        token_resp = res.json()
        if Session:
            save_tokens_db(token_resp, realm)
        else:
            save_tokens_file(token_resp, realm)
        logger.info("OAuth callback completed for realm %s", realm)
        return redirect(FRONTEND_URL + '/?connected=true')
    except Exception:
        logger.exception("Exception during token exchange")
        return ("Token exchange exception", 500)

# Simple API key check
def check_api_key(req):
    key = req.headers.get('x-api-key') or req.args.get('api_key')
    return key == RECEIPTS_API_KEY

# Normalize SalesReceipt and extract IssuedBy from LocationRef, SalesRepRef or MetaData.CreateBy
def normalize_sales_receipt(item):
    issued_by = ''
    loc = item.get('LocationRef') or {}
    if isinstance(loc, dict):
        issued_by = loc.get('name') or loc.get('Value') or loc.get('value') or ''
    if not issued_by:
        sr = (item.get('SalesRepRef') or {}).get('name') or (item.get('SalesRepRef') or {}).get('value')
        if sr:
            issued_by = sr
    if not issued_by:
        issued_by = (item.get('MetaData') or {}).get('CreateBy') or (item.get('MetaData') or {}).get('CreateById') or ''

    rec = {
        'id': item.get('Id'),
        'txn_date': item.get('TxnDate'),
        'customer': (item.get('CustomerRef') or {}).get('name') or '',
        'bill_email': (item.get('BillEmail') or {}).get('Address') or '',
        'total_amt': item.get('TotalAmt'),
        'meta': {'IssuedBy': issued_by},
        'line_items': []
    }
    for line in item.get('Line', []):
        li = {
            'item_ref': (line.get('SalesItemLineDetail') or {}).get('ItemRef', {}).get('name') or '',
            'description': line.get('Description') or '',
            'amount': line.get('Amount') or 0
        }
        rec['line_items'].append(li)
    return rec

@app.route('/receipts')
def receipts():
    if not check_api_key(request):
        return jsonify({'error':'unauthorized'}), 401
    t = refresh_tokens_if_needed()
    if not t:
        return jsonify({'error':'no_tokens'}), 400
    access_token = t.get('access_token') or (t.get('raw') or {}).get('access_token')
    realm = t.get('realm_id') or (t.get('raw') or {}).get('realmId') or QBO_REALM_ID
    if not access_token or not realm:
        return jsonify({'error':'missing_credentials'}), 400

    # Query SalesReceipt
    url = f'https://quickbooks.api.intuit.com/v3/company/{realm}/query'
    query_text = 'select * from SalesReceipt order by MetaData.CreateTime desc maxresults 50'
    headers = {'Authorization': f'Bearer {access_token}', 'Accept':'application/json'}
    try:
        r = requests.get(url, params={'query': query_text}, headers=headers, timeout=20)
        if r.status_code == 401:
            refresh_tokens_if_needed()
            t = get_tokens()
            access_token = t.get('access_token') or (t.get('raw') or {}).get('access_token')
            headers['Authorization'] = f'Bearer {access_token}'
            r = requests.get(url, params={'query': query_text}, headers=headers, timeout=20)
        if r.status_code != 200:
            logger.error('QBO query failed: %s %s', r.status_code, r.text)
            return jsonify({'error':'qbo_query_failed','status':r.status_code,'text':r.text}), 500
        data = r.json()
        items = data.get('QueryResponse', {}).get('SalesReceipt', [])
        receipts = [normalize_sales_receipt(it) for it in items]
        receipts = sorted(receipts, key=lambda r: r.get('txn_date') or '', reverse=True)[:50]
        return jsonify({'receipts': receipts})
    except Exception:
        logger.exception("Exception querying QBO")
        return jsonify({'error':'exception'}), 500

# Optional single receipt endpoint
@app.route('/receipt/<rid>')
def get_receipt(rid):
    if not check_api_key(request):
        return jsonify({'error':'unauthorized'}), 401
    t = refresh_tokens_if_needed()
    if not t:
        return jsonify({'error':'no_tokens'}), 400
    access_token = t.get('access_token') or (t.get('raw') or {}).get('access_token')
    realm = t.get('realm_id') or (t.get('raw') or {}).get('realmId') or QBO_REALM_ID
    url = f'https://quickbooks.api.intuit.com/v3/company/{realm}/salesreceipt/{rid}'
    headers = {'Authorization': f'Bearer {access_token}', 'Accept':'application/json'}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return jsonify({'error':'qbo_query_failed','status':r.status_code}), 500
        item = r.json().get('SalesReceipt')
        if not item:
            return jsonify({'error':'not_found'}), 404
        return jsonify({'receipt': normalize_sales_receipt(item)})
    except Exception:
        logger.exception("Exception fetching single receipt")
        return jsonify({'error':'exception'}), 500

if __name__ == '__main__':
    # Local debug server
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG')=='1')
