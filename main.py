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


async def create_tripay_qris(merchant_ref: str, amount: int, customer_name: str, customer_phone: str) -> Dict[str, Any]:
    if not PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL belum diset (dibutuhkan untuk callback_url Tripay)")

    url = f"{TRIPAY_BASE_URL}/transaction/create"

    data = {
        "method": TRIPAY_QRIS_METHOD,
        "merchant_ref": merchant_ref,
        "amount": amount,
        "customer_name": customer_name,
        "customer_email": "noemail@example.com",
        "customer_phone": customer_phone,
        "order_items": [
            {
                "sku": "ORDER-PRODUCT",
                "name": "Order Produk Digital",
                "price": amount,
                "quantity": 1
            }
        ],
        "callback_url": f"{PUBLIC_BASE_URL}/tripay/callback",
        "return_url": "https://wa.me/" + customer_phone,
        "expired_time": 60 * 60,
        "signature": generate_tripay_signature(merchant_ref, amount),
    }

    headers = {
        "Authorization": f"Bearer {TRIPAY_API_KEY}"
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=data, headers=headers)

    if resp.status_code != 200:
        print("TRIPAY ERROR:", resp.status_code, resp.text)
        raise HTTPException(status_code=500, detail="Gagal membuat transaksi Tripay")

    return resp.json()

# ===================== USER & STATS =====================

def get_or_create_user(phone: str, name: str) -> Dict[str, Any]:
    users = load_json(USERS_FILE, {})
    if phone not in users:
        users[phone] = {
            "name": name,
            "saldo": 0,
            "total_spent": 0,
            "orders": []
        }
        stats = load_json(STATS_FILE, {"total_sold": 0, "total_amount": 0, "total_users": 0})
        stats["total_users"] = stats.get("total_users", 0) + 1
        save_json(STATS_FILE, stats)
        save_json(USERS_FILE, users)
    return users[phone]


def update_user(phone: str, data: Dict[str, Any]):
    users = load_json(USERS_FILE, {})
    users[phone] = data
    save_json(USERS_FILE, users)


def add_stats_sold(amount: int):
    stats = load_json(STATS_FILE, {"total_sold": 0, "total_amount": 0, "total_users": 0})
    stats["total_sold"] = stats.get("total_sold", 0) + 1
    stats["total_amount"] = stats.get("total_amount", 0) + amount
    save_json(STATS_FILE, stats)

# ===================== TEXT BUILDERS =====================

def build_menu_text(phone: str, name: str, user: Dict[str, Any]) -> str:
    stats = load_json(STATS_FILE, {"total_sold": 0, "total_amount": 0, "total_users": 0})

    msg = (
        f"Halo kak {name} 👋\n\n"
        f"User Info:\n"
        f"• WA: {phone}\n"
        f"• Saldo: Rp {user['saldo']:,}\n"
        f"• Total pembelian: Rp {user.get('total_spent', 0):,}\n"
        f"• Produk dibeli: {len(user.get('orders', []))}x\n\n"
        f"BOT Stats:\n"
        f"• Terjual: {stats.get('total_sold', 0)} pcs\n"
        f"• Total Transaksi: Rp {stats.get('total_amount', 0):,}\n"
        f"• Total User: {stats.get('total_users', 0)}\n\n"
        f"Shortcut:\n"
        f"- *menu* → Menu utama\n"
        f"- *produk* → List produk\n"
        f"- *saldo* → Cek saldo\n"
        f"- *topup 20000* → Topup via QRIS\n"
        f"- *buynow box 1* → Beli via saldo\n"
        f"- *buyqr box 1* → Beli via QRIS\n\n"
        f"Cheapest All Apps • Managed by @VanzzSkyyID"
    )
    return msg


def build_category_list_text() -> str:
    categories = load_json(CATEGORIES_FILE, {})
    if not categories:
        return "Belum ada kategori produk.\nSilakan isi file *data/categories.json*."

    lines = ["📦 *LIST PRODUK TERSEDIA:*", ""]
    for no in sorted(categories.keys(), key=lambda x: int(x)):
        lines.append(f"[{no}]. {categories[no]}")
    lines.append("")
    lines.append("Ketik *angka* untuk melihat produk dalam kategori.")
    return "\n".join(lines)


def build_category_stock_text(category_no: str) -> str:
    categories = load_json(CATEGORIES_FILE, {})
    kategori_nama = categories.get(category_no, f"Kategori {category_no}")

    products = load_json(PRODUCTS_FILE, {})
    kategori_produk = [
        {"code": code, **p}
        for code, p in products.items()
        if str(p.get("category")) == str(category_no) and p.get("active", True)
    ]

    if not kategori_produk:
        return f"Belum ada produk di kategori *{kategori_nama}* kak."

    lines = [f"📦 *STOK {kategori_nama.upper()}*"]
    for p in kategori_produk:
        code = p["code"]
        name = p["name"]
        price = int(p["price"])
        stock = int(p.get("stock", 0))
        sold = int(p.get("sold", 0))
        desc = p.get("desc", "-")

        lines.append("")
        lines.append(f"┌─ {{ *{name}* }} ─")
        lines.append(f"│ 🔐 *Kode* : {code.lower()}")
        lines.append(f"│ 🏷 *Harga* : Rp {price:,} - Stok: {stock}")
        lines.append(f"│ 🧾 *Terjual* : {sold}")
        lines.append(f"│ 📋 *Desk* : {desc}")
        lines.append(f"│ ✍ *Saldo* : buynow {code.lower()} 1")
        lines.append(f"│ ✍ *Qris*  : buyqr {code.lower()} 1")
        lines.append("└──────────────────")

    return "\n".join(lines)

# ===================== LOGIC TOPUP & ORDER =====================

async def handle_topup(phone: str, name: str, low_text: str) -> str:
    parts = low_text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return "Format topup: *topup 20000* (tanpa titik/koma)"

    amount = int(parts[1])
    if amount < 5000:
        return "Minimal topup Rp 5.000 ya kak 🙏"

    merchant_ref = f"TOPUP-{phone}-{uuid.uuid4().hex[:8]}"

    topups = load_json(TOPUPS_FILE, {})
    topups[merchant_ref] = {
        "phone": phone,
        "name": name,
        "amount": amount,
        "status": "PENDING"
    }
    save_json(TOPUPS_FILE, topups)

    result = await create_tripay_qris(merchant_ref, amount, name, phone)
    data = result.get("data", {})

    pay_url = data.get("checkout_url", "-")
    ref = data.get("reference", "-")

    msg = (
        f"Topup saldo Rp {amount:,} berhasil dibuat 🎉\n\n"
        f"• Ref: {ref}\n"
        f"• Merchant Ref: {merchant_ref}\n\n"
        f"Silakan bayar via QRIS di link berikut:\n{pay_url}\n\n"
        f"Setelah pembayaran berhasil, saldo akan otomatis masuk."
    )
    return msg


async def handle_buynow(phone: str, name: str, code: str, qty: int, user: Dict[str, Any]) -> str:
    products = load_json(PRODUCTS_FILE, {})
    if code not in products or not products[code].get("active", True):
        return f"Kode produk *{code}* tidak ditemukan.\nKetik *produk* untuk lihat list."

    product = products[code]
    price_total = int(product["price"]) * qty
    nama_produk = product["name"] + (f" x{qty}" if qty > 1 else "")

    if user["saldo"] < price_total:
        return (
            f"Saldo kak kurang.\nHarga {nama_produk}: Rp {price_total:,}\n"
            f"Saldo kamu: Rp {user['saldo']:,}\n\n"
            f"Ketik *topup 20000* (atau nominal lain) untuk isi saldo."
        )

    user["saldo"] -= price_total
    user["total_spent"] = user.get("total_spent", 0) + price_total
    user.setdefault("orders", []).append({
        "code": code,
        "name": nama_produk,
        "price": price_total
    })
    update_user(phone, user)
    add_stats_sold(price_total)

    msg = (
        f"Pembelian *{nama_produk}* via saldo berhasil ✅\n"
        f"Saldo tersisa: Rp {user['saldo']:,}\n\n"
        f"(Auto-kirim akun belum dihubungkan, nanti bisa disesuaikan dengan stok lo.)"
    )
    return msg


async def handle_buyqr(phone: str, name: str, code: str, qty: int) -> str:
    products = load_json(PRODUCTS_FILE, {})
    if code not in products or not products[code].get("active", True):
        return f"Kode produk *{code}* tidak ditemukan.\nKetik *produk* untuk lihat list."

    product = products[code]
    unit_price = int(product["price"])
    amount = unit_price * qty
    nama_produk = product["name"] + (f" x{qty}" if qty > 1 else "")

    merchant_ref = f"BUY-{code}-{phone}-{uuid.uuid4().hex[:6]}"

    result = await create_tripay_qris(
        merchant_ref=merchant_ref,
        amount=amount,
        customer_name=name,
        customer_phone=phone
    )

    data = result.get("data", {})
    pay_url = data.get("checkout_url", "-")
    ref = data.get("reference", "-")

    msg = (
        f"Order *{nama_produk}* via QRIS berhasil dibuat 🎉\n\n"
        f"• Ref: {ref}\n"
        f"• Merchant Ref: {merchant_ref}\n"
        f"• Total: Rp {amount:,}\n\n"
        f"Silakan bayar via QRIS di link berikut:\n{pay_url}\n\n"
        f"Setelah pembayaran sukses, produk akan diproses admin / auto-bot."
    )
    return msg

# ===================== ROOT & MODELS =====================

@app.get("/")
async def root():
    return {"status": "ok", "msg": "WA Tripay QRIS AutoOrder (Headless) by @VanzzSkyyID"}


class WACommand(BaseModel):
    phone: str
    name: str
    text: str

# ===================== /wa/command (UNTUK BAILEYS/UBOT) =====================

@app.post("/wa/command")
async def wa_command(cmd: WACommand):
    phone = cmd.phone
    name = cmd.name or "Kak"
    text = cmd.text.strip()
    low = text.lower()

    user = get_or_create_user(phone, name)
    categories = load_json(CATEGORIES_FILE, {})

    if low in ["menu", "start", "/start", "halo", "hi"]:
        return {"reply": build_menu_text(phone, name, user)}

    if low in ["saldo", "/saldo"]:
        return {"reply": f"Saldo kak {name} saat ini: Rp {user['saldo']:,}"}

    if low.startswith("topup"):
        reply = await handle_topup(phone, name, low)
        return {"reply": reply}

    if low in ["produk", "list", "/stock", "stock", "stok"]:
        return {"reply": build_category_list_text()}

    if low.startswith("buynow"):
        parts = low.split()
        if len(parts) < 2:
            return {"reply": "Format: *buynow kode qty*\nContoh: buynow box 1"}
        code = parts[1].upper()
        qty = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        reply = await handle_buynow(phone, name, code, qty, user)
        return {"reply": reply}

    if low.startswith("buyqr"):
        parts = low.split()
        if len(parts) < 2:
            return {"reply": "Format: *buyqr kode qty*\nContoh: buyqr box 1"}
        code = parts[1].upper()
        qty = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        reply = await handle_buyqr(phone, name, code, qty)
        return {"reply": reply}

    if low.isdigit() and low in categories:
        return {"reply": build_category_stock_text(low)}

    help_text = (
        "Kak, ketik salah satu:\n"
        "- *menu* → info akun & stats bot\n"
        "- *produk* → list kategori produk\n"
        "- *saldo* → cek saldo\n"
        "- *topup 20000* → topup saldo via QRIS\n"
        "- *buynow kode 1* → beli via saldo (contoh: buynow box 1)\n"
        "- *buyqr kode 1* → beli via QRIS langsung\n"
        "- *angka kategori* (contoh: 10) → lihat stok dalam kategori tersebut"
    )
    return {"reply": help_text}

# ===================== TRIPAY CALLBACK =====================

@app.post("/tripay/callback")
async def tripay_callback(request: Request):
    body = await request.json()
    callback_signature = request.headers.get("X-Callback-Signature", "")
    expected = hmac.new(
        BASE_CALLBACK_SECRET.encode("utf-8"),
        str(body).encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if callback_signature and callback_signature != expected:
        print("WARNING: Callback signature mismatch")

    status = body.get("status")
    merchant_ref = body.get("merchant_ref")
    amount = body.get("amount")

    print("=== TRIPAY CALLBACK ===")
    print("Merchant Ref:", merchant_ref)
    print("Status:", status)
    print("Amount:", amount)
    print("Payload:", body)
    print("=======================")

    if not merchant_ref:
        return {"success": False}

    topups = load_json(TOPUPS_FILE, {})
    top = topups.get(merchant_ref)

    if status == "PAID" and top:
        phone = top["phone"]
        name = top["name"]
        nominal = int(top["amount"])

        if top.get("status") == "PAID":
            return {"success": True}

        top["status"] = "PAID"
        topups[merchant_ref] = top
        save_json(TOPUPS_FILE, topups)

        user = get_or_create_user(phone, name)
        user["saldo"] += nominal
        update_user(phone, user)

        print(f"[TOPUP] {phone} + Rp {nominal:,} (saldo baru: Rp {user['saldo']:,})")

    return {"success": True}


# ===================== UVICORN (LOCAL DEV) =====================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
