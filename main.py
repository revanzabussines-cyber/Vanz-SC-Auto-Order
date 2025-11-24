import os
import json
import uuid
import hmac
import hashlib
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, Response
import httpx

# ===================== CONFIG & PATH =====================

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"
PRODUCTS_FILE = DATA_DIR / "products.json"
STATS_FILE = DATA_DIR / "stats.json"
TOPUPS_FILE = DATA_DIR / "topups.json"

# Tripay
TRIPAY_API_KEY = os.getenv("TRIPAY_API_KEY")
TRIPAY_PRIVATE_KEY = os.getenv("TRIPAY_PRIVATE_KEY")
TRIPAY_MERCHANT_CODE = os.getenv("TRIPAY_MERCHANT_CODE")
TRIPAY_BASE_URL = os.getenv("TRIPAY_BASE_URL", "https://tripay.co.id/api-sandbox")
TRIPAY_QRIS_METHOD = os.getenv("TRIPAY_QRIS_METHOD", "QRIS")

# Callback secret (opsional)
BASE_CALLBACK_SECRET = os.getenv("BASE_CALLBACK_SECRET", "vanz-secret")

# WhatsApp Cloud API
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "verify-vanz")

if not all([TRIPAY_API_KEY, TRIPAY_PRIVATE_KEY, TRIPAY_MERCHANT_CODE]):
    raise RuntimeError("TRIPAY env vars belum lengkap")

if not all([WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID]):
    raise RuntimeError("WhatsApp env vars belum lengkap")

WA_API_URL = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

app = FastAPI(title="WA Tripay QRIS AutoOrder by @VanzzSkyyID")

# ===================== CATEGORIES (MENU ANGKA) =====================

CATEGORIES = {
    "1": "AI CHATGPT",
    "2": "AI GEMINI",
    "3": "AI PERPLEXITY",
    "4": "ALIGHT MOTION",
    "5": "APPLE MUSIC",
    "6": "CANVA EDUCATION",
    "7": "CANVA PRO",
    "8": "CAPCUT PRO",
    "9": "DISNEY HOTSTAR",
    "10": "DRAMABOX VIP",
    "11": "EXPRESS VPN",
    "12": "HMA VPN",
    "13": "MICROSOFT 365",
    "14": "NFX UHD",
    "15": "PRIME VIDEO",
    "16": "REELSHORT VIP",
    "17": "SCRIBD 1B",
    "18": "SEWA BOT",
    "19": "SPOTIFY 30D",
    "20": "VDO TV",
    "21": "VISION PLUS",
    "22": "VIU PREMIUM",
    "23": "WETV VIP",
    "24": "WINK VIP",
    "25": "YOUTUBE PREMIUM",
    "26": "ZOOM PRO"
}

# ===================== HELPER JSON =====================

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

ensure_files()

# ===================== WHATSAPP SENDER =====================

async def wa_send_text(to: str, text: str):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(WA_API_URL, headers=headers, json=payload)
    if resp.status_code >= 400:
        print("WA SEND ERROR:", resp.status_code, resp.text)


# ===================== TRIPAY HELPER =====================

def generate_tripay_signature(merchant_ref: str, amount: int) -> str:
    payload = f"{TRIPAY_MERCHANT_CODE}{merchant_ref}{amount}"
    return hmac.new(
        TRIPAY_PRIVATE_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


async def create_tripay_qris(merchant_ref: str, amount: int, customer_name: str, customer_phone: str) -> Dict[str, Any]:
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
                "name": "Order Produk",
                "price": amount,
                "quantity": 1
            }
        ],
        "callback_url": os.getenv("PUBLIC_BASE_URL", "") + "/tripay/callback",
        "return_url": "https://wa.me/" + customer_phone,
        "expired_time": 60 * 60,
        "signature": generate_tripay_signature(merchant_ref, amount)
    }

    headers = {
        "Authorization": f"Bearer {TRIPAY_API_KEY}"
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=data, headers=headers)

    if resp.status_code != 200:
        print("TRIPAY ERROR:", resp.status_code, resp.text)
        raise HTTPException(status_code=500, detail="Gagal buat transaksi Tripay")

    return resp.json()


# ===================== USER & STATS LOGIC =====================

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


# ===================== ROOT =====================

@app.get("/")
async def root():
    return {"status": "ok", "msg": "WA Tripay QRIS AutoOrder by @VanzzSkyyID"}


# ===================== WHATSAPP WEBHOOK =====================

@app.get("/whatsapp/webhook")
async def verify_wa(request: Request):
    mode = request.query_params.get("hub.mode")
    challenge = request.query_params.get("hub.challenge")
    token = request.query_params.get("hub.verify_token")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/whatsapp/webhook")
async def wa_webhook(request: Request):
    body = await request.json()
    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages", [])
        if not messages:
            return {"status": "ignored"}
        msg = messages[0]
        from_ = msg["from"]
        name = value["contacts"][0].get("profile", {}).get("name", "Kak")
        if msg["type"] != "text":
            await wa_send_text(from_, "Kirim pesan teks saja ya kak 😊")
            return {"status": "ok"}
        text = msg["text"]["body"].strip()

        user = get_or_create_user(from_, name)
        await handle_command(from_, name, text, user)

    except Exception as e:
        print("WA WEBHOOK ERROR:", e, body)
    return {"status": "ok"}


# ===================== COMMAND HANDLER =====================

async def handle_command(phone: str, name: str, text: str, user: Dict[str, Any]):
    low = text.lower().strip()

    if low in ["menu", "start", "/start", "halo", "hi"]:
        await send_menu(phone, name, user)
        return

    if low in ["saldo", "/saldo"]:
        await wa_send_text(phone, f"Saldo kak {name} saat ini: Rp {user['saldo']:,}")
        return

    if low.startswith("topup"):
        parts = low.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await wa_send_text(phone, "Format topup: *topup 20000* (tanpa titik/koma)")
            return
        amount = int(parts[1])
        await process_topup_request(phone, name, amount)
        return

    if low in ["produk", "list", "/stock", "stock", "stok"]:
        await send_category_list(phone)
        return

    if low.startswith("buynow"):
        parts = low.split()
        if len(parts) < 2:
            await wa_send_text(phone, "Format: *buynow kode qty*\nContoh: buynow box 1")
            return
        code = parts[1].upper()
        qty = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        await process_buy_request(phone, name, code, user, qty=qty)
        return

    if low.startswith("buyqr"):
        parts = low.split()
        if len(parts) < 2:
            await wa_send_text(phone, "Format: *buyqr kode qty*\nContoh: buyqr box 1")
            return
        code = parts[1].upper()
        qty = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        await process_buy_qris_request(phone, name, code, qty)
        return

    # kalau pesan cuma angka & cocok kategori → kirim stok kategori
    if low.isdigit() and low in CATEGORIES:
        await send_category_stock(phone, low)
        return

    await wa_send_text(
        phone,
        "Kak, ketik salah satu:\n"
        "- *menu* → info akun & stats bot\n"
        "- *produk* → list kategori produk\n"
        "- *saldo* → cek saldo\n"
        "- *topup 20000* → topup saldo via QRIS\n"
        "- *beli KODE* / *buynow kode 1* → beli pakai saldo\n"
        "- *buyqr kode 1* → order produk via QRIS"
    )


async def send_menu(phone: str, name: str, user: Dict[str, Any]):
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
    await wa_send_text(phone, msg)


async def send_category_list(phone: str):
    lines = ["📦 *LIST PRODUK TERSEDIA:*", ""]
    for no in sorted(CATEGORIES.keys(), key=lambda x: int(x)):
        lines.append(f"[{no}]. {CATEGORIES[no]}")
    lines.append("")
    lines.append("Ketik *angka* untuk melihat produk dalam kategori.")
    await wa_send_text(phone, "\n".join(lines))


async def send_category_stock(phone: str, category_no: str):
    products = load_json(PRODUCTS_FILE, {})
    kategori_nama = CATEGORIES.get(category_no, f"Kategori {category_no}")

    kategori_produk = [
        {"code": code, **p}
        for code, p in products.items()
        if str(p.get("category")) == str(category_no)
    ]

    if not kategori_produk:
        await wa_send_text(phone, f"Belum ada produk di kategori *{kategori_nama}* kak.")
        return

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

    await wa_send_text(phone, "\n".join(lines))


async def process_topup_request(phone: str, name: str, amount: int):
    if amount < 5000:
        await wa_send_text(phone, "Minimal topup Rp 5.000 ya kak 🙏")
        return

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
    await wa_send_text(phone, msg)


async def process_buy_request(phone: str, name: str, code: str, user: Dict[str, Any], qty: int = 1):
    products = load_json(PRODUCTS_FILE, {})
    if code not in products:
        await wa_send_text(phone, f"Kode produk *{code}* tidak ditemukan.\nKetik *produk* untuk lihat list.")
        return

    product = products[code]
    price = int(product["price"]) * qty
    nama_produk = product["name"] + (f" x{qty}" if qty > 1 else "")

    if user["saldo"] < price:
        await wa_send_text(
            phone,
            f"Saldo kak kurang.\nHarga {nama_produk}: Rp {price:,}\n"
            f"Saldo kamu: Rp {user['saldo']:,}\n\n"
            f"Ketik *topup 20000* (atau nominal lain) untuk isi saldo."
        )
        return

    user["saldo"] -= price
    user["total_spent"] = user.get("total_spent", 0) + price
    user.setdefault("orders", []).append({
        "code": code,
        "name": nama_produk,
        "price": price
    })
    update_user(phone, user)
    add_stats_sold(price)

    msg = (
        f"Pembelian *{nama_produk}* via saldo berhasil ✅\n"
        f"Saldo tersisa: Rp {user['saldo']:,}\n\n"
        f"(Auto-kirim akun belum dihubungkan, nanti bisa kita isi sesuai stok lo.)"
    )
    await wa_send_text(phone, msg)


async def process_buy_qris_request(phone: str, name: str, code: str, qty: int = 1):
    products = load_json(PRODUCTS_FILE, {})
    if code not in products:
        await wa_send_text(phone, f"Kode produk *{code}* tidak ditemukan.\nKetik *produk* untuk lihat list.")
        return

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
        f"Setelah pembayaran sukses, admin/auto-bot bisa kirim produk."
    )
    await wa_send_text(phone, msg)


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

    # HANDLE TOPUP
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

        await wa_send_text(
            phone,
            f"Topup saldo Rp {nominal:,} *BERHASIL* ✅\nSaldo baru kamu: Rp {user['saldo']:,}"
        )

    # TODO: kalau mau auto-delivery untuk BUY-XXX (buyqr), bisa cek merchant_ref prefix "BUY-"
    return {"success": True}


# ===================== UVICORN (LOCAL) =====================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
