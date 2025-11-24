import os
import json
import uuid
import hmac
import hashlib
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import httpx

# ===================== CONFIG & PATH =====================

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"
PRODUCTS_FILE = DATA_DIR / "products.json"
STATS_FILE = DATA_DIR / "stats.json"
TOPUPS_FILE = DATA_DIR / "topups.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"

# Tripay
TRIPAY_API_KEY = os.getenv("TRIPAY_API_KEY")
TRIPAY_PRIVATE_KEY = os.getenv("TRIPAY_PRIVATE_KEY")
TRIPAY_MERCHANT_CODE = os.getenv("TRIPAY_MERCHANT_CODE")
TRIPAY_BASE_URL = os.getenv("TRIPAY_BASE_URL", "https://tripay.co.id/api-sandbox")
TRIPAY_QRIS_METHOD = os.getenv("TRIPAY_QRIS_METHOD", "QRIS")

# Callback secret (opsional, untuk verifikasi tambahan)
BASE_CALLBACK_SECRET = os.getenv("BASE_CALLBACK_SECRET", "vanz-secret")

# Domain publik backend (untuk callback_url Tripay)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

if not all([TRIPAY_API_KEY, TRIPAY_PRIVATE_KEY, TRIPAY_MERCHANT_CODE]):
    raise RuntimeError("TRIPAY_API_KEY / TRIPAY_PRIVATE_KEY / TRIPAY_MERCHANT_CODE belum diset di environment")

app = FastAPI(title="WA Tripay QRIS AutoOrder (Headless) by @VanzzSkyyID")

# ===================== JSON HELPERS =====================

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_files():
    if not USERS_FILE.exists():
        save_json(USERS_FILE, {})
    if not PRODUCTS_FILE.exists():
        save_json(PRODUCTS_FILE, {})
    if not STATS_FILE.exists():
        save_json(STATS_FILE, {"total_sold": 0, "total_amount": 0, "total_users": 0})
    if not TOPUPS_FILE.exists():
        save_json(TOPUPS_FILE, {})
    if not CATEGORIES_FILE.exists():
        save_json(CATEGORIES_FILE, {})

ensure_files()

# ===================== TRIPAY HELPERS =====================

def generate_tripay_signature(merchant_ref: str, amount: int) -> str:
    payload = f"{TRIPAY_MERCHANT_CODE}{merchant_ref}{amount}"
    return hmac.new(
        TRIPAY_PRIVATE_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


async def create_tr_
