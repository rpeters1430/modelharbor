"""Encrypted Civitai credential storage."""
import os, base64, hashlib
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

def cipher():
    key=os.environ.get("MODELHARBOR_CREDENTIAL_KEY","")
    if len(key)<32: raise HTTPException(503,"Set MODELHARBOR_CREDENTIAL_KEY on the NAS (32+ random characters)")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))

def ensure_tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS provider_credentials (provider TEXT PRIMARY KEY, secret BLOB NOT NULL)")

def configured(db):
    return bool(db.execute("SELECT 1 FROM provider_credentials WHERE provider='civitai'").fetchone())

def set_token(db, token):
    if not token.strip(): raise HTTPException(422,"Empty token")
    db.execute("INSERT INTO provider_credentials VALUES ('civitai',?) ON CONFLICT(provider) DO UPDATE SET secret=excluded.secret",(cipher().encrypt(token.strip().encode()),))

def delete_token(db):
    db.execute("DELETE FROM provider_credentials WHERE provider='civitai'")

def get_token(db):
    row=db.execute("SELECT secret FROM provider_credentials WHERE provider='civitai'").fetchone()
    if not row: return None
    try: return cipher().decrypt(row[0]).decode()
    except InvalidToken as exc: raise HTTPException(503,"Credential encryption key changed") from exc
