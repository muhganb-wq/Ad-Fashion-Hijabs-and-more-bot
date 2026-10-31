"""
AD FASHION SHOP - companion web app (installable PWA).

Runs inside the SAME process / database as the Telegram bot, so catalog, photos,
categories, prices, orders and statuses are identical in both places.
Hooked up from bot.py with:   webshop.register(app_flask, cfg)
"""
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import struct
import threading
import time
import urllib.parse
import urllib.request
import zlib
from functools import wraps

from flask import (
    Blueprint, abort, flash, g, redirect, render_template_string,
    request, send_file, session, url_for, Response,
)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("web", __name__)
CFG = {}
APP_NAME = "AD FASHION SHOP"

DELIVERY = {
    "pickup": "Pickup (Bauchi Central Market)",
    "bauchi": "Delivery within Bauchi",
    "nationwide": "Nationwide delivery",
}
STATUS_LABEL = {
    "PENDING": "Pending", "AWAITING_PAYMENT": "Awaiting payment", "PAID": "Paid",
    "PROCESSING": "Processing", "OUT_FOR_DELIVERY": "Out for delivery",
    "DELIVERY_FAILED": "Delivery failed", "DELIVERED": "Delivered", "CANCELLED": "Cancelled",
}
STEPS = ["AWAITING_PAYMENT", "PAID", "PROCESSING", "OUT_FOR_DELIVERY", "DELIVERED"]
FABRICS = [("dk", "Dan Kaduna Fabric"), ("zm", "Zoom Fabric"), ("eg", "Egyptian Fabric")]
FABRIC_NAME = dict(FABRICS)
STATUS_MESSAGES = {
    "PAID": "💳 Payment verified. Your order is confirmed and will be prepared.",
    "PROCESSING": "🛠️ Your order is being prepared.",
    "OUT_FOR_DELIVERY": "🛵 Your order is on the way. Please keep your phone available.",
    "DELIVERY_FAILED": "⚠️ We could not complete the delivery attempt. Our team will contact you.",
    "DELIVERED": "🎉 Your order has been marked as delivered. Thank you for shopping with us!",
    "CANCELLED": "❌ Your order has been cancelled. Contact us if you need help.",
    "AWAITING_PAYMENT": "⏳ Your order is waiting for payment.",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUBLIC_ENDPOINTS = {
    "web.home", "web.login", "web.register_page", "web.manifest", "web.service_worker",
    "web.icon", "web.brand_logo", "web.brand_flyer", "web.health", "web.robots",
}
_login_attempts = {}

ICONS = {
    "home": '<path d="M3 11l9-8 9 8v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    "shop": '<path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>',
    "news": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
    "orders": '<path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z"/><path d="M3.3 7L12 12l8.7-5M12 22V12"/>',
    "user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "cart": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/>',
    "heart": '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21.2l7.8-7.7 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>',
    "comment": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/>',
    "admin": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "chat": '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.2A8.4 8.4 0 0 1 12.5 3 8.5 8.5 0 0 1 21 11.5z"/>',
}


def svg_icon(name):
    return Markup('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
                  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS.get(name, "") + "</svg>")


# ------------------------------------------------------------------ db
def db():
    if "db" not in g:
        g.db = sqlite3.connect(CFG["DB"])
        g.db.row_factory = sqlite3.Row
    return g.db


def init_web_db():
    c = sqlite3.connect(CFG["DB"])
    c.executescript("""
        CREATE TABLE IF NOT EXISTS web_users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, phone TEXT DEFAULT '',
            password_hash TEXT NOT NULL, score INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS web_wishlist(
            user_id INTEGER NOT NULL, product_id TEXT NOT NULL, PRIMARY KEY(user_id, product_id));
        CREATE TABLE IF NOT EXISTS news_posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
            kind TEXT DEFAULT 'news', pinned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS news_likes(
            post_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(post_id, user_id));
        CREATE TABLE IF NOT EXISTS news_comments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            parent_id INTEGER, body TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS product_fabric_prices(
            product_id TEXT NOT NULL, fabric TEXT NOT NULL, price INTEGER NOT NULL, PRIMARY KEY(product_id, fabric));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    """)

    def addcol(table, col, ddl):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if cols and col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    addcol("orders", "web_user_id", "INTEGER")
    addcol("orders", "received_confirmed", "INTEGER DEFAULT 0")
    addcol("order_items", "fabric", "TEXT DEFAULT ''")
    addcol("news_posts", "image_file_id", "TEXT DEFAULT ''")
    c.commit()
    c.close()


@bp.teardown_app_request
def close_db(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def setting(key, default=""):
    r = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r and r["value"] is not None else default


def set_setting(key, value):
    db().execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))
    db().commit()


# ------------------------------------------------------------------ small helpers
def money(v):
    return "Price on request" if v is None else f"₦{v:,.0f}"


def slugify(text, existing=()):
    base = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:24] or "item"
    key, n = base, 2
    while key in existing:
        key = f"{base[:20]}_{n}"; n += 1
    return key


def to_int(v, default=None, lo=None, hi=None):
    try:
        n = int(str(v).replace(",", "").replace("₦", "").strip())
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def cats():
    """Live category labels from the bot (emoji stripped for the web)."""
    out = {}
    for k, v in (CFG.get("CATEGORY_LABELS") or {}).items():
        out[k] = re.sub(r"^[^\w]+", "", v).strip() or k
    return out


def clean_label(v):
    return re.sub(r"^[^\w]+", "", v or "").strip() or (v or "")


def color_categories():
    return CFG.get("COLOR_CATEGORIES", ())


# ------------------------------------------------------------------ users
def admin_emails():
    raw = (os.environ.get("ADMIN_EMAIL") or "").strip().strip("\"'")
    return {e.strip().strip("\"'").lower() for e in re.split(r"[,\s;]+", raw) if e.strip()}


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    if "user" not in g:
        g.user = db().execute("SELECT * FROM web_users WHERE id=?", (uid,)).fetchone()
        if g.user is None:
            session.clear()
    return g.user


def is_admin_user():
    u = current_user()
    return bool(u and u["email"].strip().lower() in admin_emails())


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user():
            return redirect(url_for("web.home"))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user():
            return redirect(url_for("web.login", next=request.path))
        if not is_admin_user():
            abort(403)
        return f(*a, **k)
    return w


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@bp.before_request
def guard():
    # CSRF for every POST
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
            abort(400, "Session expired. Go back and try again.")
    # Login wall: everything except the welcome/login/register pages and app assets
    if request.endpoint not in PUBLIC_ENDPOINTS and not current_user():
        if request.method == "GET":
            return redirect(url_for("web.home"))
        abort(403)


def start_session(user_id, keep=None):
    session.clear()
    session.permanent = True        # stays signed in until the user signs out
    session["uid"] = user_id
    session["csrf"] = secrets.token_hex(16)
    if keep:
        session["cart"] = keep


# ------------------------------------------------------------------ catalog helpers
def products(category=None, q=None, sub=None):
    sql, args = "SELECT * FROM products WHERE active=1", []
    if category:
        sql += " AND category=?"; args.append(category)
    if sub:
        sql += " AND subcategory=?"; args.append(sub)
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    return db().execute(sql + " ORDER BY name", args).fetchall()


def product_photos(pid):
    rows = db().execute(
        "SELECT id,file_id,label FROM product_photos WHERE product_id=? ORDER BY position,id", (pid,)).fetchall()
    out = [{"id": r["id"], "file_id": r["file_id"], "label": r["label"] or ""} for r in rows]
    if not out:
        r = db().execute("SELECT photo_file_id FROM products WHERE id=?", (pid,)).fetchone()
        if r and r["photo_file_id"]:
            out = [{"id": 0, "file_id": r["photo_file_id"], "label": ""}]
    return out


def fabric_prices(pid):
    rows = db().execute("SELECT fabric,price FROM product_fabric_prices WHERE product_id=? AND price>0", (pid,)).fetchall()
    have = {r["fabric"]: r["price"] for r in rows}
    return {k: have[k] for k, _ in FABRICS if k in have}


def unit_price(p, fab=None):
    fp = fabric_prices(p["id"])
    return fp[fab] if fab and fab in fp else p["price"]


def display_price(p):
    fp = fabric_prices(p["id"])
    return f"from {money(min(fp.values()))}" if fp else money(p["price"])


def discount_pct():
    return max(0, min(90, to_int(setting("discount_percent", "0"), 0)))


def delivery_fee(method):
    return max(0, to_int(setting(f"delivery_fee_{method}", "0"), 0))


def pricing(items_total, method="pickup"):
    off = int(items_total * discount_pct() / 100) if items_total else 0
    fee = delivery_fee(method)
    return off, fee, items_total - off + fee


# cart keys look like "pid|colorid|fabric" (colorid / fabric may be empty)
def make_key(pid, cid=None, fab=None):
    k = pid
    if cid or fab:
        k += f"|{cid or ''}"
    if fab:
        k += f"|{fab}"
    return k


def parse_key(key):
    parts = str(key).split("|")
    cid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    fab = parts[2] if len(parts) > 2 and parts[2] else None
    return parts[0], cid, fab


def cart():
    return session.setdefault("cart", {})


def cart_detail():
    items, total = [], 0
    for key, qty in list(cart().items()):
        pid, cid, fab = parse_key(key)
        p = db().execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            cart().pop(key, None); continue
        color = db().execute("SELECT * FROM color_options WHERE id=?", (cid,)).fetchone() if cid else None
        price = unit_price(p, fab) or 0
        line = price * qty
        total += line
        items.append({"key": key, "p": p, "qty": qty, "line": line, "color": color,
                      "fab": fab, "fab_name": FABRIC_NAME.get(fab, ""), "price": price})
    return items, total


# ------------------------------------------------------------------ Telegram helpers
def _api(method, data=None, files=None, timeout=15):
    url = f"https://api.telegram.org/bot{CFG['BOT_TOKEN']}/{method}"
    if files:
        boundary = secrets.token_hex(12)
        body = []
        for k, v in (data or {}).items():
            body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (fname, blob) in files.items():
            body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                        f'Content-Type: application/octet-stream\r\n\r\n'.encode())
            body.append(blob); body.append(b"\r\n")
        body.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(url, data=b"".join(body),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data or {}).encode())
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def tg_send(chat_id, text, buttons=None):
    """Best-effort Telegram message in the background (never slows or breaks a web request)."""
    def run():
        try:
            data = {"chat_id": chat_id, "text": text}
            if buttons:
                data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
            _api("sendMessage", data, timeout=10)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def tg_notify(text):
    tg_send(CFG["ADMIN_ID"], text)


def tg_upload_photo(blob, caption=""):
    """Upload an image through the bot so it gets a Telegram file_id (same storage as the bot).
    Returns the file_id or None."""
    try:
        res = _api("sendPhoto", {"chat_id": CFG["ADMIN_ID"], "caption": caption, "disable_notification": "true"},
                   files={"photo": ("upload.jpg", blob)}, timeout=30)
        return res["result"]["photo"][-1]["file_id"]
    except Exception:
        return None


def read_image_upload(field):
    """Return bytes of an uploaded image or None (validates the file type and size)."""
    f = request.files.get(field)
    if not f or not f.filename:
        return None
    blob = f.read(9_000_000)
    ok = blob[:3] == b"\xff\xd8\xff" or blob[:4] == b"\x89PNG" or (blob[:4] == b"RIFF" and blob[8:12] == b"WEBP")
    return blob if ok else None


# ------------------------------------------------------------------ images, brand, icons
def _cache_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(CFG["DB"])), "web_cache")
    os.makedirs(d, exist_ok=True)
    return d


def fetch_file(fid):
    """Download a Telegram file by file_id (cached on disk). Returns a path or None."""
    path = os.path.join(_cache_dir(), hashlib.sha256(fid.encode()).hexdigest() + ".img")
    if not os.path.exists(path):
        try:
            base = f"https://api.telegram.org/bot{CFG['BOT_TOKEN']}"
            info = json.load(urllib.request.urlopen(f"{base}/getFile?file_id={urllib.parse.quote(fid)}", timeout=10))
            blob = urllib.request.urlopen(
                f"https://api.telegram.org/file/bot{CFG['BOT_TOKEN']}/{info['result']['file_path']}", timeout=25).read()
            with open(path, "wb") as f:
                f.write(blob)
        except Exception:
            return None
    return path


def _known_file_id(fid):
    d = db()
    for sql in ("SELECT 1 FROM products WHERE photo_file_id=?", "SELECT 1 FROM product_photos WHERE file_id=?",
                "SELECT 1 FROM color_options WHERE photo_file_id=?", "SELECT 1 FROM news_posts WHERE image_file_id=?",
                "SELECT 1 FROM orders WHERE receipt_file_id=?",
                "SELECT 1 FROM settings WHERE value=? AND key IN ('web_logo','web_flyer','welcome_photo')"):
        if d.execute(sql, (fid,)).fetchone():
            return True
    return False


@bp.get("/img/<path:fid>")
def img(fid):
    if not _known_file_id(fid):
        abort(404)
    path = fetch_file(fid)
    if not path:
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=86400 * 30)


def _default_logo_png(size):
    """Pure-python fallback logo (black with a gold ring) used until the real logo is uploaded."""
    rows = []
    c = size / 2
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dist = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            gold = c * 0.62 < dist < c * 0.72
            row += bytes((201, 162, 75) if gold else (17, 17, 17))
        rows.append(bytes(row))

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


@bp.get("/brand/logo")
def brand_logo():
    fid = setting("web_logo")
    path = fetch_file(fid) if fid else None
    if path:
        return send_file(path, mimetype="image/jpeg", max_age=3600)
    return Response(_default_logo_png(256), mimetype="image/png")


@bp.get("/brand/flyer")
def brand_flyer():
    fid = setting("web_flyer")
    path = fetch_file(fid) if fid else None
    if not path:
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@bp.get("/icon-<int:size>.png")
def icon(size):
    if size not in (192, 512):
        abort(404)
    fid = setting("web_logo")
    src = fetch_file(fid) if fid else None
    out = os.path.join(_cache_dir(), f"icon_{hashlib.sha256((fid or 'default').encode()).hexdigest()[:12]}_{size}.png")
    if not os.path.exists(out):
        data = None
        if src:
            try:
                from PIL import Image
                im = Image.open(src).convert("RGB")
                w, h = im.size
                side = min(w, h)
                im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize((size, size))
                buf = io.BytesIO(); im.save(buf, "PNG"); data = buf.getvalue()
            except Exception:
                data = None
        if data is None and src:
            with open(src, "rb") as f:      # no Pillow available: use the logo as it is
                data = f.read()
        if data is None:
            data = _default_logo_png(size)
        with open(out, "wb") as f:
            f.write(data)
    r = send_file(out, mimetype="image/png", max_age=3600)
    return r


# ================================================================== templates
BASE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{{ title ~ ' · ' if title }}{{ app_name }}</title>
<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#111111">
<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{{ app_name }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#f7f3ec;--ink:#16130f;--mut:#7b7266;--card:#fff;--line:#e6dccb;--gold:#b8892f;--black:#111;--ok:#1b7a4b;--bad:#b3261e;--soft:#f0e8d8}
@media (prefers-color-scheme:dark){:root{--bg:#0f0e0c;--ink:#f4ede1;--mut:#a59b8b;--card:#1a1815;--line:#2e2a23;--gold:#d8aa4a;--soft:#25211b}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,sans-serif;padding-bottom:calc(76px + env(safe-area-inset-bottom))}
body.noauth{padding-bottom:0}
a{color:inherit}h1,h2,h3{font-family:'Cormorant Garamond',Georgia,serif;font-weight:700;line-height:1.15;margin:.15em 0}
h1{font-size:30px}h2{font-size:25px}h3{font-size:20px}
svg{width:22px;height:22px;display:block}
header.top{position:sticky;top:0;z-index:30;background:var(--black);color:#fff;padding-top:env(safe-area-inset-top)}
.bar{max-width:1000px;margin:auto;padding:10px 14px;display:flex;align-items:center;gap:10px}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;flex:1;min-width:0}
.brand img{width:36px;height:36px;border-radius:50%;object-fit:cover;border:1.5px solid var(--gold);background:#000}
.brand b{font:700 19px 'Cormorant Garamond',serif;color:#e6c36b;letter-spacing:.8px;white-space:nowrap}
.ib{position:relative;color:#fff;padding:6px;display:block;text-decoration:none}
.badge{position:absolute;top:-2px;right:-3px;background:var(--gold);color:#111;font:700 10px Inter;border-radius:99px;min-width:17px;height:17px;display:flex;align-items:center;justify-content:center;padding:0 4px}
main{max-width:1000px;margin:auto;padding:16px 14px}
nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:30;background:var(--card);border-top:1px solid var(--line);display:flex;justify-content:space-around;padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
nav.tabs a{flex:1;max-width:120px;text-decoration:none;color:var(--mut);display:flex;flex-direction:column;align-items:center;gap:2px;font-size:11px;font-weight:500;padding:4px 0}
nav.tabs a.on{color:var(--gold);font-weight:700}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
@media(min-width:620px){.grid{grid-template-columns:repeat(3,1fr)}}@media(min-width:900px){.grid{grid-template-columns:repeat(4,1fr)}}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
a.pc{text-decoration:none;display:block;position:relative}
.pc img{width:100%;aspect-ratio:4/5;object-fit:cover;display:block;background:var(--soft)}
.pc .b{padding:9px 11px 11px}.pc .t{font-weight:600;font-size:14px;line-height:1.3}
.price{color:var(--gold);font-weight:700}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:0;background:var(--black);color:#e6c36b;padding:13px 22px;border-radius:14px;font:600 15px Inter;cursor:pointer;text-decoration:none}
.btn.gold{background:linear-gradient(135deg,#d8aa4a,#b8892f);color:#111}
.btn.alt{background:transparent;color:var(--ink);border:1.5px solid var(--line)}
.btn.wa{background:#1f9d55;color:#fff}.btn.bad{background:var(--bad);color:#fff}.btn.sm{padding:8px 14px;font-size:13px;border-radius:10px}
.btn.block{display:flex;width:100%}
.pills{display:flex;gap:8px;overflow-x:auto;padding:2px 0 12px;scrollbar-width:none}.pills::-webkit-scrollbar{display:none}
.pill{white-space:nowrap;padding:8px 16px;border:1px solid var(--line);border-radius:99px;text-decoration:none;font-size:13px;background:var(--card);font-weight:500}
.pill.on{background:var(--black);color:#e6c36b;border-color:var(--black)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.between{justify-content:space-between}.grow{flex:1}
.mut{color:var(--mut)}.small{font-size:13px}
input,select,textarea{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);font:inherit}
input[type=checkbox],input[type=radio]{width:auto}
label.l{display:block;margin:12px 0 5px;font-size:13px;color:var(--mut);font-weight:500}
.flash{background:var(--soft);border-left:4px solid var(--gold);padding:11px 14px;border-radius:10px;margin-bottom:12px;font-weight:500}
.hscroll{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:6px;scrollbar-width:none}.hscroll::-webkit-scrollbar{display:none}
.hscroll>*{flex:0 0 150px;scroll-snap-align:start}
.tile{position:relative;border-radius:16px;overflow:hidden;aspect-ratio:16/10;background:var(--black);display:flex;align-items:flex-end;text-decoration:none;color:#fff}
.tile img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.7}
.tile span{position:relative;padding:12px;font:700 19px 'Cormorant Garamond',serif;text-shadow:0 1px 6px #000}
.sec{display:flex;align-items:baseline;justify-content:space-between;margin:22px 0 10px}.sec a{font-size:13px;color:var(--gold);text-decoration:none;font-weight:600}
.promo{background:linear-gradient(135deg,#d8aa4a,#b8892f);color:#111;border-radius:14px;padding:12px 16px;font-weight:700;text-align:center;margin-bottom:14px}
.flyer{width:100%;border-radius:18px;display:block;margin-bottom:14px}
/* gallery */
.gal{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;border-radius:18px;scrollbar-width:none;background:var(--soft)}.gal::-webkit-scrollbar{display:none}
.gal figure{margin:0;flex:0 0 100%;scroll-snap-align:center;position:relative}
.gal img{width:100%;aspect-ratio:4/5;object-fit:cover;display:block}
.gal figcaption{position:absolute;left:12px;bottom:12px;background:rgba(0,0,0,.65);color:#fff;padding:4px 12px;border-radius:99px;font-size:13px}
.dots{display:flex;justify-content:center;gap:6px;margin:10px 0}.dots i{width:7px;height:7px;border-radius:50%;background:var(--line)}.dots i.on{background:var(--gold);width:20px;border-radius:9px}
.two{display:grid;gap:20px}@media(min-width:760px){.two{grid-template-columns:1fr 1fr}}
.opt{display:flex;align-items:center;gap:12px;padding:12px 14px;border:1.5px solid var(--line);border-radius:14px;margin-bottom:8px;cursor:pointer;background:var(--card)}
.opt input{accent-color:#b8892f}.opt:has(input:checked){border-color:var(--gold);background:var(--soft)}
.swatches{display:flex;gap:10px;flex-wrap:wrap}.sw input{display:none}.sw img{width:62px;height:62px;object-fit:cover;border-radius:14px;border:3px solid transparent;display:block}.sw input:checked+img{border-color:var(--gold)}
.qty{display:inline-flex;align-items:center;border:1.5px solid var(--line);border-radius:12px;overflow:hidden;background:var(--card)}
.qty button{width:42px;height:42px;border:0;background:none;font-size:20px;color:var(--ink);cursor:pointer}.qty input{width:56px;border:0;text-align:center;padding:0;border-radius:0}
.tag{display:inline-block;padding:3px 10px;border-radius:99px;background:var(--soft);font-size:12px;font-weight:600}.tag.g{background:var(--gold);color:#111}.tag.ok{background:var(--ok);color:#fff}.tag.bad{background:var(--bad);color:#fff}
.li{display:flex;gap:12px;padding:12px;align-items:center}.li img.th{width:68px;height:80px;object-fit:cover;border-radius:10px;background:var(--soft)}
.prog{display:flex;gap:6px;margin:14px 0}.prog div{flex:1;text-align:center;font-size:10.5px;color:var(--mut)}.prog b{display:block;height:8px;border-radius:9px;background:var(--line);margin-bottom:6px}.prog .d{color:var(--ink)}.prog .d b{background:var(--ok)}
.avatar{width:72px;height:72px;border-radius:50%;background:var(--black);color:#e6c36b;display:flex;align-items:center;justify-content:center;font:700 28px 'Cormorant Garamond',serif;border:2px solid var(--gold)}
.menu a,.menu button{display:flex;align-items:center;gap:14px;padding:14px 16px;text-decoration:none;border:0;border-bottom:1px solid var(--line);background:none;width:100%;font:inherit;color:inherit;cursor:pointer;text-align:left}
.menu a:last-child{border-bottom:0}.menu svg{color:var(--gold)}
.post img.cover{width:100%;max-height:360px;object-fit:cover;display:block}
.post .pb{padding:14px 16px}
.act{display:flex;gap:18px;padding:0 16px 14px;align-items:center}
.act button,.act a{display:flex;gap:6px;align-items:center;background:none;border:0;color:var(--mut);font:600 14px Inter;cursor:pointer;text-decoration:none;padding:0}
.act .liked{color:#d4284a}.act .liked svg{fill:currentColor}
.cm{padding:10px 0;border-bottom:1px solid var(--line)}.cm.re{margin-left:26px;border-left:2px solid var(--soft);padding-left:12px}
.cm .who{font-weight:700;font-size:13px}.cm p{margin:.2em 0;white-space:pre-wrap}
.kpi{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}@media(min-width:700px){.kpi{grid-template-columns:repeat(4,1fr)}}
.kpi div{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}.kpi b{display:block;font:700 26px 'Cormorant Garamond',serif;color:var(--gold)}
table{width:100%;border-collapse:collapse}td,th{padding:10px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;font-size:14px}
.splash{position:fixed;inset:0;background:#111;z-index:100;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;transition:opacity .5s}
.splash img{width:132px;height:132px;border-radius:50%;object-fit:cover;border:2px solid #d8aa4a;animation:pop .9s ease}
.splash b{font:700 26px 'Cormorant Garamond',serif;color:#e6c36b;letter-spacing:2px}
@keyframes pop{from{transform:scale(.6);opacity:0}to{transform:scale(1);opacity:1}}
.welcome{min-height:100vh;display:flex;flex-direction:column;background:#111;color:#fff}
.welcome .fl{flex:1;display:flex;align-items:center;justify-content:center;padding:18px}
.welcome .fl img{max-width:100%;max-height:62vh;border-radius:18px;box-shadow:0 10px 40px rgba(0,0,0,.6)}
.welcome .acts{padding:18px 18px calc(24px + env(safe-area-inset-bottom));display:grid;gap:10px;max-width:480px;width:100%;margin:auto}
.welcome .alt{color:#fff;border-color:#555}
#inst{display:none;position:fixed;left:12px;right:12px;bottom:90px;background:#111;color:#e6c36b;padding:12px 16px;border-radius:14px;z-index:40;align-items:center;gap:12px;border:1px solid var(--gold)}
</style></head><body class="{{ '' if user else 'noauth' }}">
<div class="splash" id="splash"><img src="/brand/logo" alt=""><b>{{ app_name }}</b></div>
{% if user %}
<header class="top"><div class="bar">
<a class="brand" href="{{ url_for('web.home') }}"><img src="/brand/logo" alt=""><b>{{ app_name }}</b></a>
<a class="ib" href="{{ url_for('web.shop') }}" aria-label="Search">{{ icon('search') }}</a>
<a class="ib" href="{{ url_for('web.cart_view') }}" aria-label="Cart">{{ icon('cart') }}{% if cart_count %}<span class="badge">{{ cart_count }}</span>{% endif %}</a>
{% if is_admin %}<a class="ib" href="{{ url_for('web.admin_home') }}" aria-label="Admin" style="color:#e6c36b">{{ icon('admin') }}</a>{% endif %}
</div></header>
{% endif %}
<main>{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}{{ body|safe }}</main>
{% if user %}
<nav class="tabs">
{% for key,label,ep in [('home','Home','web.home'),('shop','Shop','web.shop'),('news','News','web.news'),('orders','My Orders','web.orders'),('user','Profile','web.profile')] %}
<a href="{{ url_for(ep) }}" class="{{ 'on' if tab==key }}">{{ icon(key) }}<span>{{ label }}</span></a>{% endfor %}
</nav>{% endif %}
<div id="inst"><span style="flex:1;font-size:14px">Install <b>{{ app_name }}</b> on your phone</span>
<button class="btn gold sm" id="instbtn">Install</button><button id="instx" style="border:0;background:none;color:#e6c36b;font-size:18px">✕</button></div>
<script>
(function(){var s=document.getElementById('splash');var seen=false;try{seen=sessionStorage.getItem('splashed')}catch(e){}
function hide(){s.style.opacity=0;setTimeout(function(){s.style.display='none'},500);try{sessionStorage.setItem('splashed','1')}catch(e){}}
if(seen){s.style.display='none'}else{window.addEventListener('load',function(){setTimeout(hide,1100)});setTimeout(hide,3500)}})();
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){});}
var dp=null;window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();dp=e;var b=document.getElementById('inst');var x=false;try{x=sessionStorage.getItem('instx')}catch(_){}if(!x)b.style.display='flex';});
document.getElementById('instbtn').onclick=function(){if(dp){dp.prompt();dp=null;}document.getElementById('inst').style.display='none';};
document.getElementById('instx').onclick=function(){document.getElementById('inst').style.display='none';try{sessionStorage.setItem('instx','1')}catch(e){}};
</script></body></html>"""

PCARD = """{% macro pcard(p) %}<a class="card pc" href="{{ url_for('web.product', pid=p['id']) }}">
{% if p['photo_file_id'] %}<img loading="lazy" src="{{ url_for('web.img', fid=p['photo_file_id']) }}" alt="{{ p['name'] }}">{% else %}<img alt="">{% endif %}
<div class="b"><div class="t">{{ p['name'] }}</div><div class="price">{{ display_price(p) }}</div></div></a>{% endmacro %}"""


def page(title, body_tpl, tab="", **ctx):
    common = dict(csrf=csrf_token(), money=money, cfg=CFG, user=current_user(), status_label=STATUS_LABEL,
                  icon=svg_icon, display_price=display_price, app_name=APP_NAME, is_admin=is_admin_user(),
                  fabric_name=FABRIC_NAME)
    body = render_template_string(PCARD + body_tpl, **common, **ctx)
    return render_template_string(BASE, title=title, body=body, tab=tab, cart_count=sum(cart().values()),
                                  **{k: common[k] for k in ("user", "icon", "app_name", "is_admin")})


def safe_next(default):
    n = request.form.get("next") or request.args.get("next") or ""
    return n if n.startswith("/") and not n.startswith("//") else default


# ================================================================== PWA files
@bp.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": APP_NAME, "short_name": APP_NAME, "id": "/", "start_url": "/", "scope": "/",
        "display": "standalone", "orientation": "portrait", "background_color": "#111111", "theme_color": "#111111",
        "description": "AD FASHION HIJABS AND MORE - hijabs, jilbabs, textiles and more.",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json")


@bp.get("/sw.js")
def service_worker():
    js = """
const CACHE='ad-shop-v2';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim());});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(fetch(e.request).then(r=>{
    if(r.ok&&(e.request.url.includes('/img/')||e.request.url.includes('/brand/'))){const c=r.clone();caches.open(CACHE).then(k=>k.put(e.request,c));}
    return r;
  }).catch(()=>caches.match(e.request).then(m=>m||new Response('You are offline. Please reconnect to keep shopping.',{status:503,headers:{'Content-Type':'text/plain'}}))));
});
"""
    r = Response(js, mimetype="application/javascript")
    r.headers["Service-Worker-Allowed"] = "/"
    r.headers["Cache-Control"] = "no-cache"
    return r


@bp.get("/health")
def health():
    return {"status": "ok", "service": CFG.get("NAME"), "web": True}


@bp.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


# ================================================================== welcome / auth
WELCOME = """
<div class="welcome"><div class="fl">
<img src="/brand/flyer" alt="AD FASHION HIJABS AND MORE" onerror="this.style.display='none';document.getElementById('fb').style.display='block'">
<div id="fb" style="display:none;text-align:center"><img src="/brand/logo" style="width:150px;height:150px;border-radius:50%;border:2px solid #d8aa4a;object-fit:cover" alt="">
<h1 style="color:#e6c36b;margin-top:16px;letter-spacing:1px">AD FASHION HIJABS AND MORE</h1><p style="opacity:.8">{{ cfg.TAGLINE }}</p></div>
</div>
<div class="acts"><a class="btn gold block" href="{{ url_for('web.register_page') }}">Create account</a>
<a class="btn alt block" href="{{ url_for('web.login') }}">Log in</a>
<p class="small" style="text-align:center;opacity:.6;margin:4px 0 0">Create an account to start shopping</p></div></div>"""

AUTH_FORM = """<div style="max-width:420px;margin:0 auto;padding:10px 0">
<div style="text-align:center;margin:10px 0 18px"><img src="/brand/logo" style="width:86px;height:86px;border-radius:50%;border:2px solid var(--gold);object-fit:cover" alt="">
<h2 style="margin-top:10px">{{ heading }}</h2><p class="mut small">{{ sub }}</p></div>
<form method="post" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
{% if register %}<label class="l">Full name</label><input name="name" required autocomplete="name">{% endif %}
<label class="l">Email</label><input type="email" name="email" required autocomplete="email" autocapitalize="off">
<label class="l">Password</label><input type="password" name="password" required minlength="6" autocomplete="{{ 'new-password' if register else 'current-password' }}">
<p><button class="btn gold block">{{ button }}</button></p></form>
<p style="text-align:center">{{ switch|safe }}</p></div>"""


@bp.route("/", methods=["GET"])
def home():
    u = current_user()
    if not u:
        return page("Welcome", WELCOME)
    d = db()
    new = d.execute("SELECT * FROM products WHERE active=1 ORDER BY rowid DESC LIMIT 10").fetchall()
    tiles = []
    for k, label in cats().items():
        r = d.execute("SELECT photo_file_id FROM products WHERE category=? AND active=1 AND photo_file_id!='' LIMIT 1", (k,)).fetchone()
        tiles.append({"key": k, "label": label, "img": r["photo_file_id"] if r else ""})
    latest = d.execute("SELECT * FROM news_posts ORDER BY pinned DESC, id DESC LIMIT 2").fetchall()
    return page("Home", """
{% if pct %}<div class="promo">🎉 {{ pct }}% OFF everything — applied automatically at checkout!</div>{% endif %}
<h2>Assalamu alaikum, {{ user['name'].split()[0] }} 👋</h2><p class="mut" style="margin-top:0">What would you like to wear today?</p>
{% if flyer %}<img class="flyer" src="/brand/flyer" alt="" onerror="this.remove()">{% endif %}
<div class="grid" style="grid-template-columns:repeat(2,1fr)">{% for t in tiles %}<a class="tile" href="{{ url_for('web.shop', c=t.key) }}">
{% if t.img %}<img src="{{ url_for('web.img', fid=t.img) }}" alt="">{% endif %}<span>{{ t.label }}</span></a>{% endfor %}</div>
<div class="sec"><h3>New arrivals</h3><a href="{{ url_for('web.shop') }}">See all</a></div>
<div class="hscroll">{% for p in new %}{{ pcard(p) }}{% endfor %}</div>
{% if latest %}<div class="sec"><h3>Latest news</h3><a href="{{ url_for('web.news') }}">All news</a></div>
{% for n in latest %}<a class="card" href="{{ url_for('web.news_post', nid=n['id']) }}#p" style="display:block;padding:14px;margin-bottom:10px;text-decoration:none">
<span class="tag {{ 'g' if n['kind']=='giveaway' }}">{{ n['kind']|title }}</span><h3>{{ n['title'] }}</h3>
<p class="mut small" style="margin:.2em 0">{{ n['body'][:110] }}{% if n['body']|length>110 %}…{% endif %}</p></a>{% endfor %}{% endif %}
<p><a class="btn wa block" href="https://wa.me/{{ cfg.WHATSAPP }}">{{ icon('chat') }} Chat with us on WhatsApp</a></p>
""", tab="home", pct=discount_pct(), tiles=tiles, new=new, latest=latest, flyer=bool(setting("web_flyer")))


@bp.route("/register", methods=["GET", "POST"])
def register_page():
    if current_user():
        return redirect(url_for("web.home"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80]
        email = request.form.get("email", "").strip().lower()[:120]
        pw = request.form.get("password", "")
        if not name or not EMAIL_RE.match(email) or len(pw) < 6:
            flash("Enter your name, a valid email and a password of 6+ characters.")
        elif db().execute("SELECT 1 FROM web_users WHERE email=?", (email,)).fetchone():
            flash("That email already has an account. Please log in.")
        else:
            cur = db().execute("INSERT INTO web_users(email,name,password_hash) VALUES(?,?,?)",
                               (email, name, generate_password_hash(pw)))
            db().commit()
            start_session(cur.lastrowid)
            flash("Welcome to AD FASHION SHOP! You've earned 10 style points.")
            return redirect(url_for("web.home"))
    return page("Create account", AUTH_FORM, register=True, heading="Create your account",
                sub="No email verification needed.", button="Create account",
                switch=Markup('Already have an account? <a href="%s"><b>Log in</b></a>' % url_for("web.login")))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("web.home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]
        hits = [t for t in _login_attempts.get(ip, []) if time.time() - t < 600]
        if len(hits) >= 10:
            flash("Too many attempts. Try again in a few minutes.")
        else:
            u = db().execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()
            if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
                start_session(u["id"], session.get("cart"))
                return redirect(safe_next(url_for("web.home")))
            _login_attempts[ip] = hits + [time.time()]
            flash("Wrong email or password.")
    return page("Log in", AUTH_FORM, register=False, heading="Welcome back", sub="Log in to continue shopping.",
                button="Log in", switch=Markup('New here? <a href="%s"><b>Create an account</b></a>' % url_for("web.register_page")))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.home"))


# ================================================================== shop
@bp.get("/shop")
def shop():
    c, q = request.args.get("c"), (request.args.get("q") or "").strip()
    subs = CFG.get("SUBCATEGORIES", {}).get(c, []) if c in cats() else []
    sub = request.args.get("s") if request.args.get("s") in [k for k, _ in subs] else None
    items = products(c if c in cats() else None, q or None, sub)
    return page("Shop", """
<form method="get" class="row" style="flex-wrap:nowrap"><input name="q" value="{{ q }}" placeholder="Search hijabs, jilbabs, fabrics…" style="flex:1"><button class="btn sm">Search</button></form>
<div class="pills" style="margin-top:12px"><a class="pill {{ 'on' if not c }}" href="{{ url_for('web.shop') }}">All</a>
{% for k,v in cats.items() %}<a class="pill {{ 'on' if c==k }}" href="{{ url_for('web.shop', c=k) }}">{{ v }}</a>{% endfor %}</div>
{% if subs %}<div class="pills">{% for k,lab in subs %}<a class="pill {{ 'on' if sub==k }}" href="{{ url_for('web.shop', c=c, s=k) }}">{{ clean(lab) }}</a>{% endfor %}</div>{% endif %}
{% if not items %}<p class="mut">Nothing found.</p>{% endif %}
<div class="grid">{% for p in items %}{{ pcard(p) }}{% endfor %}</div>""",
                tab="shop", items=items, cats=cats(), c=c, q=q, subs=subs, sub=sub, clean=clean_label)


@bp.get("/p/<pid>")
def product(pid):
    p = db().execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not p:
        abort(404)
    photos = product_photos(pid)
    is_color_cat = (p["category"] or "").lower() in color_categories()
    colors = db().execute("SELECT * FROM color_options WHERE active=1 ORDER BY id").fetchall() if is_color_cat else []
    fp = fabric_prices(pid) if is_color_cat else {}
    u = current_user()
    wished = bool(db().execute("SELECT 1 FROM web_wishlist WHERE user_id=? AND product_id=?", (u["id"], pid)).fetchone())
    share = f"https://wa.me/?text={urllib.parse.quote(p['name'] + ' — ' + request.url)}"
    return page(p["name"], """
<div class="two"><div>
<div class="gal" id="gal">{% for ph in photos %}<figure><img src="{{ url_for('web.img', fid=ph.file_id) }}" alt="">
{% if ph.label %}<figcaption>{{ ph.label }}</figcaption>{% endif %}</figure>{% else %}<figure><img alt=""></figure>{% endfor %}</div>
{% if photos|length>1 %}<div class="dots" id="dots">{% for ph in photos %}<i class="{{ 'on' if loop.first }}"></i>{% endfor %}</div>
<p class="mut small" style="text-align:center;margin:0">Swipe to see all {{ photos|length }} photos →</p>{% endif %}
</div><div>
<h1>{{ p['name'] }}</h1>
<div class="price" style="font-size:24px" id="price">{{ display_price(p) }}</div>
<p>{{ p['description'] or '' }}</p>
{% if p['variety'] %}<p><b>Variety:</b> {{ p['variety'] }}</p>{% endif %}
{% if p['features'] %}<p><b>Features:</b> {{ p['features'] }}</p>{% endif %}
<form method="post" action="{{ url_for('web.cart_add') }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="pid" value="{{ p['id'] }}">
{% if fp %}<label class="l">Choose fabric</label>
{% for k,v in fp.items() %}<label class="opt"><input type="radio" name="fabric" value="{{ k }}" required {{ 'checked' if loop.first }}><span class="grow"><b>{{ fabric_name[k] }}</b></span><span class="price">{{ money(v) }}</span></label>{% endfor %}{% endif %}
{% if colors %}<label class="l">Choose colour</label><div class="swatches">
{% for co in colors %}<label class="sw"><input type="radio" name="color" value="{{ co['id'] }}" {{ 'checked' if loop.first }}><img src="{{ url_for('web.img', fid=co['photo_file_id']) }}" alt="Colour {{ co['id'] }}"></label>{% endfor %}</div>{% endif %}
<label class="l">Quantity</label>
<div class="qty"><button type="button" onclick="st(-1)">−</button><input type="number" name="qty" id="qty" value="1" min="1" max="500" oninput="upd()"><button type="button" onclick="st(1)">+</button></div>
<div class="row" style="margin-top:18px"><button class="btn gold grow">{{ icon('cart') }} Add to cart</button>
<a class="btn wa" href="{{ share }}" target="_blank" rel="noopener">Share</a></div></form>
<form method="post" action="{{ url_for('web.wish', pid=p['id']) }}" style="margin-top:10px"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="btn alt sm">{{ icon('heart') }} {{ 'Saved to wishlist' if wished else 'Save for later' }}</button></form>
</div></div>
<script>
var FP={{ fp|tojson }},BASE={{ p['price']|tojson }};
function upd(){var f=document.querySelector('input[name=fabric]:checked');var u=f?FP[f.value]:BASE;var q=Math.max(1,parseInt(document.getElementById('qty').value)||1);
var el=document.getElementById('price');if(u==null){return}el.textContent='₦'+(u*q).toLocaleString()+(q>1?'  (₦'+u.toLocaleString()+' each)':'');}
function st(n){var i=document.getElementById('qty');i.value=Math.min(500,Math.max(1,(parseInt(i.value)||1)+n));upd();}
document.querySelectorAll('input[name=fabric]').forEach(function(r){r.addEventListener('change',upd)});if(Object.keys(FP).length)upd();
var g=document.getElementById('gal'),ds=document.querySelectorAll('#dots i');
if(g&&ds.length){g.addEventListener('scroll',function(){var i=Math.round(g.scrollLeft/g.clientWidth);ds.forEach(function(d,k){d.className=k==i?'on':''})});}
</script>""", tab="shop", p=p, photos=photos, colors=colors, fp=fp, wished=wished, share=share)


@bp.post("/wish/<pid>")
def wish(pid):
    u = current_user()
    if db().execute("SELECT 1 FROM web_wishlist WHERE user_id=? AND product_id=?", (u["id"], pid)).fetchone():
        db().execute("DELETE FROM web_wishlist WHERE user_id=? AND product_id=?", (u["id"], pid))
    else:
        db().execute("INSERT INTO web_wishlist VALUES(?,?)", (u["id"], pid))
    db().commit()
    return redirect(url_for("web.product", pid=pid))


@bp.get("/wishlist")
def wishlist():
    rows = db().execute("""SELECT p.* FROM products p JOIN web_wishlist w ON w.product_id=p.id
                           WHERE w.user_id=? AND p.active=1""", (current_user()["id"],)).fetchall()
    return page("Wishlist", "<h2>My wishlist</h2>{% if not items %}<p class=mut>Nothing saved yet.</p>{% endif %}"
                "<div class=grid>{% for p in items %}{{ pcard(p) }}{% endfor %}</div>", tab="user", items=rows)


# ------------------------------------------------------------------ cart
@bp.post("/cart/add")
def cart_add():
    pid = request.form.get("pid", "")
    p = db().execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not p:
        abort(404)
    qty = to_int(request.form.get("qty"), 1, 1, 500)
    is_color_cat = (p["category"] or "").lower() in color_categories()
    fab, cid = None, None
    if is_color_cat:
        fp = fabric_prices(pid)
        if fp:
            fab = request.form.get("fabric")
            if fab not in fp:
                flash("Please choose a fabric first.")
                return redirect(url_for("web.product", pid=pid))
        c = request.form.get("color", "")
        if c.isdigit() and db().execute("SELECT 1 FROM color_options WHERE id=? AND active=1", (c,)).fetchone():
            cid = int(c)
    key = make_key(pid, cid, fab)
    cart()[key] = min(500, cart().get(key, 0) + qty)
    session.modified = True
    flash(f"Added {p['name']} to your cart.")
    return redirect(url_for("web.cart_view"))


@bp.post("/cart/update")
def cart_update():
    key = request.form.get("key", "")
    qty = to_int(request.form.get("qty"), 1)
    if key in cart():
        if qty <= 0:
            cart().pop(key, None)
        else:
            cart()[key] = min(qty, 500)
    session.modified = True
    return redirect(url_for("web.cart_view"))


@bp.get("/cart")
def cart_view():
    items, total = cart_detail()
    off = pricing(total)[0]
    return page("Cart", """
<h2>Your cart</h2>{% if not items %}<div class="card" style="padding:30px;text-align:center"><p class="mut">Your cart is empty.</p>
<a class="btn gold" href="{{ url_for('web.shop') }}">Start shopping</a></div>{% else %}
{% for i in items %}<div class="card li" style="margin-bottom:10px">
{% if i.p['photo_file_id'] %}<img class="th" src="{{ url_for('web.img', fid=i.p['photo_file_id']) }}" alt="">{% endif %}
<div class="grow"><b>{{ i.p['name'] }}</b>
{% if i.fab_name %}<div class="small"><span class="tag">{{ i.fab_name }}</span></div>{% endif %}
{% if i.color %}<div class="small mut"><img src="{{ url_for('web.img', fid=i.color['photo_file_id']) }}" style="width:22px;height:22px;border-radius:6px;object-fit:cover;vertical-align:middle"> Colour #{{ i.color['id'] }}</div>{% endif %}
<div class="price">{{ money(i.line) }}</div>
<form method="post" action="{{ url_for('web.cart_update') }}" class="row" style="margin-top:6px;gap:6px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="key" value="{{ i.key }}">
<div class="qty"><button name="qty" value="{{ i.qty-1 }}" style="width:36px;height:36px">−</button><input value="{{ i.qty }}" readonly style="width:40px"><button name="qty" value="{{ i.qty+1 }}" style="width:36px;height:36px">+</button></div>
<button name="qty" value="0" class="btn alt sm" style="padding:6px 10px" aria-label="Remove">{{ icon('trash') }}</button></form></div></div>{% endfor %}
<div class="card" style="padding:16px">
{% if off %}<div class="row between mut"><span>Subtotal</span><span>{{ money(total) }}</span></div>
<div class="row between" style="color:var(--ok);font-weight:700"><span>{{ pct }}% store discount</span><span>-{{ money(off) }}</span></div>{% endif %}
<div class="row between"><h3>Total</h3><h3 class="price">{{ money(total-off) }}</h3></div>
<p class="mut small" style="margin:.3em 0 12px">Delivery fee (if any) is added at checkout.</p>
<a class="btn gold block" href="{{ url_for('web.checkout') }}">Checkout</a></div>{% endif %}""",
                tab="shop", items=items, total=total, off=off, pct=discount_pct())


# ------------------------------------------------------------------ checkout / orders
@bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    items, total = cart_detail()
    if not items:
        return redirect(url_for("web.cart_view"))
    u = current_user()
    if setting("accepting_orders", "1") == "0":
        flash("We are not accepting new orders right now. Please check back soon.")
        return redirect(url_for("web.cart_view"))
    if request.method == "POST":
        method = request.form.get("method", "pickup")
        if method not in DELIVERY:
            method = "pickup"
        phone = re.sub(r"[^\d+]", "", request.form.get("phone", ""))[:20]
        addr = request.form.get("address", "").strip()[:300]
        city = request.form.get("city", "").strip()[:80]
        state = request.form.get("state", "").strip()[:80]
        notes = request.form.get("notes", "").strip()[:300]
        if len(phone) < 7 or (method != "pickup" and not addr):
            flash("Please give a phone number and (for delivery) an address.")
            return redirect(url_for("web.checkout"))
        lines = []
        for i in items:
            extra = (f" ({i['fab_name']})" if i["fab_name"] else "") + (f" [Colour #{i['color']['id']}]" if i["color"] else "")
            lines.append(f"{i['p']['name']}{extra} x{i['qty']} - {money(i['line'])}")
        off, fee, grand = pricing(total, method)
        if off:
            lines.append(f"{discount_pct()}% store discount: -{money(off)}")
        if fee:
            lines.append(f"Delivery fee: {money(fee)}")
        d = db()
        cur = d.execute(
            """INSERT INTO orders(user_id,username,customer_name,phone,address,items,total,status,
               delivery_method,city,state,delivery_notes,product_ids,web_user_id)
               VALUES(0,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("web:" + u["email"], u["name"], phone, addr or CFG["ADDRESS"], "\n".join(lines), grand,
             "AWAITING_PAYMENT", method, city, state, notes, ",".join(i["key"] for i in items), u["id"]))
        oid = cur.lastrowid
        for i in items:
            d.execute("""INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price,color_id,color_photo_file_id,fabric)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (oid, i["p"]["id"], i["p"]["name"], i["qty"], i["price"],
                       i["color"]["id"] if i["color"] else None,
                       i["color"]["photo_file_id"] if i["color"] else "", i["fab"] or ""))
        d.execute("UPDATE web_users SET score=score+5, phone=? WHERE id=?", (phone, u["id"]))
        d.commit()
        session["cart"] = {}
        tg_notify(f"🌐 New APP order #AD-{oid:05d}\n{u['name']} ({u['email']})\n{phone}\n{DELIVERY[method]}\n"
                  f"{addr} {city} {state}\n\n" + "\n".join(lines) + f"\n\nTotal: {money(grand)}")
        return redirect(url_for("web.order", oid=oid))
    off = pricing(total)[0]
    return page("Checkout", """
<h2>Checkout</h2>
<div class="card" style="padding:14px;margin-bottom:12px"><div class="row between"><span class="mut">{{ items|length }} item(s){% if off %} · {{ pct }}% discount applied{% endif %}</span><b class="price">{{ money(total-off) }}</b></div>
<p class="mut small" style="margin:.4em 0 0">Delivery fees: {% for k,v in dm.items() %}{{ v }} — {{ money(fees[k]) if fees[k] else 'free / confirmed by us' }}{{ '; ' if not loop.last }}{% endfor %}</p></div>
<form method="post" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Delivery method</label><select name="method">{% for k,v in dm.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select>
<label class="l">Phone number</label><input name="phone" value="{{ user['phone'] }}" required inputmode="tel">
<label class="l">Address (for delivery)</label><textarea name="address" rows="2"></textarea>
<div class="row" style="flex-wrap:nowrap"><div class="grow"><label class="l">City</label><input name="city"></div><div class="grow"><label class="l">State</label><input name="state"></div></div>
<label class="l">Notes (optional)</label><input name="notes">
<p><button class="btn gold block">Place order</button></p></form>""",
                tab="shop", total=total, items=items, dm=DELIVERY, off=off, pct=discount_pct(),
                fees={k: delivery_fee(k) for k in DELIVERY})


def own_order(oid):
    return db().execute("SELECT * FROM orders WHERE id=? AND web_user_id=?", (oid, current_user()["id"])).fetchone()


@bp.get("/order/<int:oid>")
def order(oid):
    o = own_order(oid)
    if not o:
        abort(404)
    paystack = bool(os.environ.get("PAYSTACK_SECRET"))
    return page(f"Order #AD-{oid:05d}", """
<div class="row between"><h2>Order #AD-{{ '%05d'|format(o['id']) }}</h2><span class="tag g">{{ status_label.get(o['status'], o['status']) }}</span></div>
{% if o['status'] in steps %}{% set cur = steps.index(o['status']) %}
<div class="prog">{% for st in steps %}<div class="{{ 'd' if loop.index0<=cur }}"><b></b>{{ status_label[st] }}</div>{% endfor %}</div>
{% elif o['status'] in ('CANCELLED','DELIVERY_FAILED') %}<p class="mut">Please contact us on WhatsApp about this order.</p>{% endif %}
<div class="card" style="padding:16px"><pre style="white-space:pre-wrap;font:inherit;margin:0">{{ o['items'] }}</pre>
<div class="row between" style="margin-top:10px"><h3>Total</h3><h3 class="price">{{ money(o['total']) }}</h3></div></div>
{% if o['status']=='DELIVERED' %}
<div class="card" style="padding:18px;margin-top:14px;text-align:center">
{% if o['received_confirmed'] %}<h3 style="color:var(--ok)">✅ Thank you!</h3><p class="mut">You confirmed that you received this order. We appreciate you. 🖤</p>
{% else %}<p>Has your order reached you?</p><form method="post" action="{{ url_for('web.received', oid=o['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="btn gold block" style="font-size:16px">{{ icon('check') }} RECEIVED AND THANK YOU</button></form>{% endif %}</div>{% endif %}
{% if o['status'] in ('AWAITING_PAYMENT','PENDING') %}
<div class="card" style="padding:16px;margin:14px 0"><h3>Pay by bank transfer</h3>
{% for bank,name,num in cfg.PAYMENT_ACCOUNTS %}<p style="margin:.6em 0">{{ bank }} — <b style="font-size:17px">{{ num }}</b><br><span class="mut small">{{ name }}</span></p>{% endfor %}
<p class="mut small">Use AD-{{ '%05d'|format(o['id']) }} as the narration, then upload your receipt below.</p>
<form method="post" action="{{ url_for('web.receipt', oid=o['id']) }}" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{{ csrf }}">
<input type="file" name="receipt" accept="image/*" required><p><button class="btn gold block">Send receipt</button></p></form>
{% if o['payment_notified'] %}<p class="small" style="color:var(--ok)">✔ Receipt received — we'll confirm shortly.</p>{% endif %}</div>
{% if paystack %}<form method="post" action="{{ url_for('web.paystack_start', oid=o['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="btn alt block">Pay online with card / USSD</button></form>{% endif %}{% endif %}
<p style="margin-top:14px"><a class="btn wa block" href="https://wa.me/{{ cfg.WHATSAPP }}?text=Hello, about order AD-{{ '%05d'|format(o['id']) }}">{{ icon('chat') }} Chat on WhatsApp</a></p>""",
                tab="orders", o=o, paystack=paystack, steps=STEPS)


@bp.post("/order/<int:oid>/receipt")
def receipt(oid):
    o = own_order(oid)
    if not o:
        abort(404)
    blob = read_image_upload("receipt")
    if not blob:
        flash("Please upload a JPG or PNG image of your receipt.")
        return redirect(url_for("web.order", oid=oid))
    fid = tg_upload_photo(blob, f"🧾 Receipt for APP order #AD-{oid:05d} ({o['customer_name']}) — {money(o['total'])}")
    db().execute("UPDATE orders SET payment_notified=1, payment_method='APP_RECEIPT', receipt_file_id=? WHERE id=?",
                 (fid or "", oid))
    db().commit()
    if not fid:
        tg_notify(f"🧾 Receipt uploaded for APP order #AD-{oid:05d} but it could not be forwarded. Check the Orders page in the app.")
    flash("Receipt received. We'll confirm your payment shortly.")
    return redirect(url_for("web.order", oid=oid))


@bp.post("/order/<int:oid>/received")
def received(oid):
    o = own_order(oid)
    if not o or o["status"] != "DELIVERED":
        abort(404)
    db().execute("UPDATE orders SET received_confirmed=1 WHERE id=?", (oid,))
    db().commit()
    tg_notify(f"✅ RECEIVED AND THANK YOU — {o['customer_name']} confirmed order #AD-{oid:05d} was received.")
    flash("Thank you for shopping with AD FASHION SHOP! 🖤")
    return redirect(url_for("web.order", oid=oid))


# optional Paystack (set PAYSTACK_SECRET + SITE_URL env vars)
@bp.post("/order/<int:oid>/paystack")
def paystack_start(oid):
    key, o = os.environ.get("PAYSTACK_SECRET"), own_order(oid)
    if not key or not o:
        abort(404)
    site = (os.environ.get("SITE_URL") or request.host_url).rstrip("/")
    body = json.dumps({"email": current_user()["email"], "amount": int(o["total"]) * 100,
                       "reference": f"AD{oid}-{secrets.token_hex(4)}",
                       "callback_url": f"{site}{url_for('web.paystack_cb', oid=oid)}"}).encode()
    req = urllib.request.Request("https://api.paystack.co/transaction/initialize", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        return redirect(json.load(urllib.request.urlopen(req, timeout=15))["data"]["authorization_url"])
    except Exception:
        flash("Online payment is unavailable right now. Please use bank transfer.")
        return redirect(url_for("web.order", oid=oid))


@bp.get("/order/<int:oid>/paystack/callback")
def paystack_cb(oid):
    key, ref, o = os.environ.get("PAYSTACK_SECRET"), request.args.get("reference", ""), own_order(oid)
    if not (key and o and ref.startswith(f"AD{oid}-")):
        abort(400)
    try:
        req = urllib.request.Request(f"https://api.paystack.co/transaction/verify/{urllib.parse.quote(ref)}",
                                     headers={"Authorization": f"Bearer {key}"})
        d = json.load(urllib.request.urlopen(req, timeout=15))["data"]
        if d.get("status") == "success" and int(d.get("amount", 0)) >= int(o["total"]) * 100:
            db().execute("UPDATE orders SET status='PAID', payment_method='PAYSTACK' WHERE id=?", (oid,))
            db().commit()
            tg_notify(f"💳 Paystack payment confirmed for APP order #AD-{oid:05d} ({money(o['total'])})")
            flash("Payment confirmed. Thank you!")
        else:
            flash("Payment not completed.")
    except Exception:
        flash("Could not verify payment yet. If you were charged, contact us on WhatsApp.")
    return redirect(url_for("web.order", oid=oid))


@bp.get("/orders")
def orders():
    rows = db().execute("SELECT * FROM orders WHERE web_user_id=? ORDER BY id DESC", (current_user()["id"],)).fetchall()
    return page("My orders", """<h2>My orders</h2>
{% if not rows %}<div class="card" style="padding:30px;text-align:center"><p class="mut">No orders yet.</p><a class="btn gold" href="{{ url_for('web.shop') }}">Start shopping</a></div>{% endif %}
{% for o in rows %}<a class="card" href="{{ url_for('web.order', oid=o['id']) }}" style="display:block;padding:14px;margin-bottom:10px;text-decoration:none">
<div class="row between"><b>#AD-{{ '%05d'|format(o['id']) }}</b><span class="tag {{ 'ok' if o['status']=='DELIVERED' }}">{{ status_label.get(o['status'], o['status']) }}</span></div>
<div class="row between" style="margin-top:4px"><span class="mut small">{{ o['created_at'][:10] }}</span><b class="price">{{ money(o['total']) }}</b></div>
{% if o['status']=='DELIVERED' and not o['received_confirmed'] %}<div class="small" style="color:var(--gold);font-weight:700;margin-top:6px">Tap to confirm: RECEIVED AND THANK YOU</div>{% endif %}</a>{% endfor %}""",
                tab="orders", rows=rows)


# ================================================================== profile
@bp.get("/profile")
def profile():
    u = current_user()
    n = db().execute("SELECT COUNT(*) AS n FROM orders WHERE web_user_id=?", (u["id"],)).fetchone()["n"]
    return page("Profile", """
<div class="card" style="padding:18px"><div class="row" style="gap:16px"><div class="avatar">{{ user['name'][:1]|upper }}</div>
<div class="grow"><h2>{{ user['name'] }}</h2><div class="mut small">{{ user['email'] }}</div></div></div>
<div class="kpi" style="margin-top:14px"><div><span class="mut small">Style points</span><b>{{ user['score'] }}</b></div><div><span class="mut small">Orders</span><b>{{ n }}</b></div></div>
<p class="mut small" style="margin:.8em 0 0">Earn 10 points for joining and 5 for every order.</p></div>
<div class="card menu" style="margin-top:14px">
<a href="{{ url_for('web.orders') }}">{{ icon('orders') }} My orders</a>
<a href="{{ url_for('web.wishlist') }}">{{ icon('heart') }} Wishlist</a>
<a href="https://wa.me/{{ cfg.WHATSAPP }}">{{ icon('chat') }} Contact us on WhatsApp</a>
{% if is_admin %}<a href="{{ url_for('web.admin_home') }}">{{ icon('admin') }} Admin panel</a>{% endif %}</div>
<h3 style="margin-top:20px">Edit profile</h3>
<form method="post" action="{{ url_for('web.profile_update') }}" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Full name</label><input name="name" value="{{ user['name'] }}" required>
<label class="l">Phone</label><input name="phone" value="{{ user['phone'] }}" inputmode="tel">
<p><button class="btn block">Save</button></p></form>
<h3 style="margin-top:20px">Change password</h3>
<form method="post" action="{{ url_for('web.password_change') }}" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Current password</label><input type="password" name="old" required autocomplete="current-password">
<label class="l">New password</label><input type="password" name="new" minlength="6" required autocomplete="new-password">
<p><button class="btn block">Change password</button></p></form>
<form method="post" action="{{ url_for('web.logout') }}" style="margin-top:20px"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="btn bad block">Sign out</button></form>
<p class="mut small" style="text-align:center">You stay signed in on this phone until you sign out.</p>""", tab="user", n=n)


@bp.post("/profile")
def profile_update():
    name = request.form.get("name", "").strip()[:80]
    phone = re.sub(r"[^\d+]", "", request.form.get("phone", ""))[:20]
    if name:
        db().execute("UPDATE web_users SET name=?, phone=? WHERE id=?", (name, phone, current_user()["id"]))
        db().commit()
        flash("Profile updated.")
    return redirect(url_for("web.profile"))


@bp.post("/profile/password")
def password_change():
    u = current_user()
    if not check_password_hash(u["password_hash"], request.form.get("old", "")):
        flash("Current password is wrong.")
    elif len(request.form.get("new", "")) < 6:
        flash("New password must be at least 6 characters.")
    else:
        db().execute("UPDATE web_users SET password_hash=? WHERE id=?",
                     (generate_password_hash(request.form["new"]), u["id"]))
        db().commit()
        flash("Password changed.")
    return redirect(url_for("web.profile"))


# ================================================================== news (likes + comments + replies)
def news_rows(nid=None):
    q = """SELECT n.*, (SELECT COUNT(*) FROM news_likes l WHERE l.post_id=n.id) AS likes,
           (SELECT COUNT(*) FROM news_comments c WHERE c.post_id=n.id) AS ncom,
           (SELECT COUNT(*) FROM news_likes l WHERE l.post_id=n.id AND l.user_id=?) AS mine
           FROM news_posts n """
    uid = current_user()["id"]
    if nid:
        return db().execute(q + "WHERE n.id=?", (uid, nid)).fetchall()
    return db().execute(q + "ORDER BY n.pinned DESC, n.id DESC LIMIT 50", (uid,)).fetchall()


POST_CARD = """{% macro post(n, full=False) %}<div class="card post" id="p{{ n['id'] }}" style="margin-bottom:14px;overflow:hidden">
{% if n['image_file_id'] %}<img class="cover" loading="lazy" src="{{ url_for('web.img', fid=n['image_file_id']) }}" alt="">{% endif %}
<div class="pb"><div class="row between"><span class="tag {{ 'g' if n['kind']=='giveaway' }}">{{ n['kind']|title }}</span>
<span class="mut small">{{ n['created_at'][:10] }}{% if n['pinned'] %} · 📌{% endif %}</span></div>
<h3 style="margin-top:8px"><a href="{{ url_for('web.news_post', nid=n['id']) }}" style="text-decoration:none">{{ n['title'] }}</a></h3>
<p style="white-space:pre-wrap;margin:.4em 0">{{ n['body'] if full else n['body'][:220] }}{% if not full and n['body']|length>220 %}… <a href="{{ url_for('web.news_post', nid=n['id']) }}" style="color:var(--gold);font-weight:600">Read more</a>{% endif %}</p></div>
<div class="act"><form method="post" action="{{ url_for('web.news_like', nid=n['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="next" value="{{ request.full_path }}#p{{ n['id'] }}">
<button class="{{ 'liked' if n['mine'] }}">{{ icon('heart') }} {{ n['likes'] }}</button></form>
<a href="{{ url_for('web.news_post', nid=n['id']) }}#comments">{{ icon('comment') }} {{ n['ncom'] }}</a>
{% if is_admin %}<form method="post" action="{{ url_for('web.admin_news_delete', nid=n['id']) }}" onsubmit="return confirm('Delete this post?')" style="margin-left:auto"><input type="hidden" name="csrf" value="{{ csrf }}"><button style="color:var(--bad)">{{ icon('trash') }}</button></form>{% endif %}</div></div>{% endmacro %}"""


@bp.get("/news")
def news():
    return page("News", POST_CARD + """<div class="row between"><h2>News &amp; giveaways</h2>
{% if is_admin %}<a class="btn gold sm" href="{{ url_for('web.admin_news') }}">{{ icon('plus') }} New post</a>{% endif %}</div>
{% if not rows %}<div class="card" style="padding:30px;text-align:center"><p class="mut">Nothing yet — check back soon.</p></div>{% endif %}
{% for n in rows %}{{ post(n) }}{% endfor %}""", tab="news", rows=news_rows())


@bp.get("/news/<int:nid>")
def news_post(nid):
    rows = news_rows(nid)
    if not rows:
        abort(404)
    cm = db().execute("""SELECT c.*, u.name, u.email FROM news_comments c JOIN web_users u ON u.id=c.user_id
                         WHERE c.post_id=? ORDER BY c.id""", (nid,)).fetchall()
    admins = admin_emails()
    tops = [c for c in cm if not c["parent_id"]]
    replies = {}
    for c in cm:
        if c["parent_id"]:
            replies.setdefault(c["parent_id"], []).append(c)
    return page(rows[0]["title"], POST_CARD + """
{{ post(n, True) }}
<h3 id="comments">Comments</h3>
<form method="post" action="{{ url_for('web.news_comment', nid=n['id']) }}" class="card" style="padding:12px;margin-bottom:10px"><input type="hidden" name="csrf" value="{{ csrf }}">
<textarea name="body" rows="2" placeholder="Write a comment…" required maxlength="1000"></textarea><p style="margin:8px 0 0"><button class="btn gold sm">Post comment</button></p></form>
{% macro cmt(c, re=False) %}<div class="cm {{ 're' if re }}" id="c{{ c['id'] }}"><div class="who">{{ c['name'] }}{% if c['email'] in admins %} <span class="tag g">Admin</span>{% endif %}
<span class="mut small" style="font-weight:400"> · {{ c['created_at'][:16] }}</span></div><p>{{ c['body'] }}</p>
<div class="row" style="gap:14px">{% if not re %}<a href="#reply{{ c['id'] }}" onclick="document.getElementById('rf{{ c['id'] }}').style.display='block';return false" class="small" style="color:var(--gold);font-weight:700;text-decoration:none">Reply</a>{% endif %}
{% if is_admin or c['user_id']==user['id'] %}<form method="post" action="{{ url_for('web.news_comment_delete', cid=c['id']) }}" onsubmit="return confirm('Delete this comment?')"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="next" value="/news/{{ n['id'] }}#comments"><button style="border:0;background:none;color:var(--bad);font-size:12px;cursor:pointer;padding:0">Delete</button></form>{% endif %}</div>
{% if not re %}<form method="post" action="{{ url_for('web.news_comment', nid=n['id']) }}" id="rf{{ c['id'] }}" style="display:none;margin-top:8px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="parent_id" value="{{ c['id'] }}">
<textarea name="body" rows="2" required maxlength="1000">@{{ c['name'].split()[0] }} </textarea><p style="margin:6px 0 0"><button class="btn sm">Send reply</button></p></form>{% endif %}</div>{% endmacro %}
{% for c in tops %}{{ cmt(c) }}{% for r in replies.get(c['id'], []) %}{{ cmt(r, True) }}{% endfor %}{% endfor %}
{% if not tops %}<p class="mut">No comments yet. Be the first!</p>{% endif %}""",
                tab="news", n=rows[0], tops=tops, replies=replies, admins=admins)


@bp.post("/news/<int:nid>/like")
def news_like(nid):
    uid = current_user()["id"]
    if not db().execute("SELECT 1 FROM news_posts WHERE id=?", (nid,)).fetchone():
        abort(404)
    if db().execute("SELECT 1 FROM news_likes WHERE post_id=? AND user_id=?", (nid, uid)).fetchone():
        db().execute("DELETE FROM news_likes WHERE post_id=? AND user_id=?", (nid, uid))
    else:
        db().execute("INSERT INTO news_likes VALUES(?,?)", (nid, uid))
    db().commit()
    return redirect(safe_next(url_for("web.news")))


@bp.post("/news/<int:nid>/comment")
def news_comment(nid):
    post = db().execute("SELECT * FROM news_posts WHERE id=?", (nid,)).fetchone()
    body = request.form.get("body", "").strip()[:1000]
    if not post or not body:
        return redirect(url_for("web.news_post", nid=nid))
    parent = to_int(request.form.get("parent_id"))
    if parent:
        pr = db().execute("SELECT id, parent_id FROM news_comments WHERE id=? AND post_id=?", (parent, nid)).fetchone()
        parent = (pr["parent_id"] or pr["id"]) if pr else None
    u = current_user()
    cur = db().execute("INSERT INTO news_comments(post_id,user_id,parent_id,body) VALUES(?,?,?,?)",
                       (nid, u["id"], parent, body))
    db().commit()
    if not is_admin_user():
        tg_notify(f"💬 {'Reply' if parent else 'Comment'} on \"{post['title']}\" from {u['name']}:\n{body[:300]}")
    return redirect(url_for("web.news_post", nid=nid) + f"#c{cur.lastrowid}")


@bp.post("/news/comment/<int:cid>/delete")
def news_comment_delete(cid):
    c = db().execute("SELECT * FROM news_comments WHERE id=?", (cid,)).fetchone()
    if c and (is_admin_user() or c["user_id"] == current_user()["id"]):
        db().execute("DELETE FROM news_comments WHERE id=? OR parent_id=?", (cid, cid))
        db().commit()
    return redirect(safe_next(url_for("web.news")))


# ================================================================== ADMIN (manage everything inside the app)
ADMIN_NAV = """<div class="pills" style="margin-bottom:6px">
{% for ep,label in [('web.admin_home','Dashboard'),('web.admin_orders','Orders'),('web.admin_products','Products'),('web.admin_categories','Categories'),('web.admin_colors','Colours'),('web.admin_news','News'),('web.admin_settings','Settings'),('web.admin_customers','Customers')] %}
<a class="pill {{ 'on' if request.endpoint==ep }}" href="{{ url_for(ep) }}">{{ label }}</a>{% endfor %}</div>"""


def admin_page(title, body, **ctx):
    return page(title, ADMIN_NAV + body, tab="", **ctx)


def reload_cats():
    fn = CFG.get("reload_categories")
    if fn:
        fn()


@bp.get("/admin")
@admin_required
def admin_home():
    d = db()
    one = lambda sql, *a: d.execute(sql, a).fetchone()[0] or 0
    k = {
        "orders": one("SELECT COUNT(*) FROM orders"),
        "to_verify": one("SELECT COUNT(*) FROM orders WHERE status='AWAITING_PAYMENT' AND (payment_notified=1 OR receipt_file_id!='')"),
        "awaiting": one("SELECT COUNT(*) FROM orders WHERE status='AWAITING_PAYMENT'"),
        "active": one("SELECT COUNT(*) FROM orders WHERE status IN ('PAID','PROCESSING','OUT_FOR_DELIVERY')"),
        "delivered": one("SELECT COUNT(*) FROM orders WHERE status='DELIVERED'"),
        "revenue": one("SELECT SUM(total) FROM orders WHERE status IN ('PAID','PROCESSING','OUT_FOR_DELIVERY','DELIVERED')"),
        "products": one("SELECT COUNT(*) FROM products WHERE active=1"),
        "users": one("SELECT COUNT(*) FROM web_users"),
    }
    recent = d.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 8").fetchall()
    return admin_page("Admin", """<h2>Admin dashboard</h2>
<div class="kpi"><div><span class="mut small">Orders</span><b>{{ k.orders }}</b></div><div><span class="mut small">Payments to verify</span><b>{{ k.to_verify }}</b></div>
<div><span class="mut small">In progress</span><b>{{ k.active }}</b></div><div><span class="mut small">Delivered</span><b>{{ k.delivered }}</b></div>
<div><span class="mut small">Revenue (paid)</span><b>{{ money(k.revenue) }}</b></div><div><span class="mut small">Awaiting payment</span><b>{{ k.awaiting }}</b></div>
<div><span class="mut small">Products</span><b>{{ k.products }}</b></div><div><span class="mut small">App customers</span><b>{{ k.users }}</b></div></div>
<div class="sec"><h3>Recent orders</h3><a href="{{ url_for('web.admin_orders') }}">All orders</a></div>
{% for o in recent %}<a class="card" href="{{ url_for('web.admin_order', oid=o['id']) }}" style="display:block;padding:12px;margin-bottom:8px;text-decoration:none">
<div class="row between"><b>#AD-{{ '%05d'|format(o['id']) }} · {{ o['customer_name'] }}</b><span class="tag">{{ status_label.get(o['status'], o['status']) }}</span></div>
<div class="row between small mut"><span>{{ 'App' if o['web_user_id'] else 'Telegram' }} · {{ o['created_at'][:16] }}</span><b class="price">{{ money(o['total']) }}</b></div></a>{% endfor %}""", k=k, recent=recent)


# ------------------------------------------------------------------ orders
@bp.get("/admin/orders")
@admin_required
def admin_orders():
    st = request.args.get("status", "")
    if st in STATUS_LABEL:
        rows = db().execute("SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT 200", (st,)).fetchall()
    else:
        rows = db().execute("SELECT * FROM orders ORDER BY id DESC LIMIT 200").fetchall()
    return admin_page("Orders", """<h2>Orders</h2>
<div class="pills"><a class="pill {{ 'on' if not st }}" href="{{ url_for('web.admin_orders') }}">All</a>
{% for k,v in labels.items() %}<a class="pill {{ 'on' if st==k }}" href="{{ url_for('web.admin_orders', status=k) }}">{{ v }}</a>{% endfor %}</div>
{% if not rows %}<p class="mut">No orders.</p>{% endif %}
{% for o in rows %}<a class="card" href="{{ url_for('web.admin_order', oid=o['id']) }}" style="display:block;padding:12px;margin-bottom:8px;text-decoration:none">
<div class="row between"><b>#AD-{{ '%05d'|format(o['id']) }} · {{ o['customer_name'] }}</b><span class="tag">{{ status_label.get(o['status'], o['status']) }}</span></div>
<div class="row between small mut"><span>{{ 'App' if o['web_user_id'] else 'Telegram' }} · {{ o['created_at'][:16] }}{% if o['payment_notified'] or o['receipt_file_id'] %} · 🧾 receipt{% endif %}{% if o['received_confirmed'] %} · ✅ received{% endif %}</span><b class="price">{{ money(o['total']) }}</b></div></a>{% endfor %}""",
                      rows=rows, st=st, labels=STATUS_LABEL)


@bp.get("/admin/order/<int:oid>")
@admin_required
def admin_order(oid):
    o = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        abort(404)
    return admin_page(f"Order {oid}", """
<a href="{{ url_for('web.admin_orders') }}" class="small mut">← All orders</a>
<div class="row between"><h2>Order #AD-{{ '%05d'|format(o['id']) }}</h2><span class="tag g">{{ status_label.get(o['status'], o['status']) }}</span></div>
<div class="card" style="padding:14px"><p style="margin:0"><b>{{ o['customer_name'] }}</b>{% if o['web_user_id'] %} <span class="tag">App</span>{% else %} <span class="tag">Telegram {{ '@' ~ o['username'] if o['username'] }}</span>{% endif %}</p>
<p class="small" style="margin:.3em 0">📞 {{ o['phone'] }}<br>🚚 {{ dm.get(o['delivery_method'], o['delivery_method']) }}<br>📍 {{ o['address'] }} {{ o['city'] }} {{ o['state'] }}
{% if o['delivery_notes'] %}<br>📝 {{ o['delivery_notes'] }}{% endif %}<br>🕒 {{ o['created_at'] }}</p>
<pre style="white-space:pre-wrap;font:inherit;margin:.6em 0">{{ o['items'] }}</pre>
<div class="row between"><b>Total</b><b class="price">{{ money(o['total']) }}</b></div>
{% if o['received_confirmed'] %}<p style="color:var(--ok);font-weight:700;margin-bottom:0">✅ Customer confirmed: RECEIVED AND THANK YOU</p>{% endif %}</div>
{% if o['receipt_file_id'] %}<h3 style="margin-top:14px">Payment receipt</h3><img src="{{ url_for('web.img', fid=o['receipt_file_id']) }}" style="max-width:100%;border-radius:14px" alt="">{% elif o['payment_notified'] %}<p class="mut">Customer says they paid (check Telegram for the receipt).</p>{% endif %}
{% if o['status'] in ('AWAITING_PAYMENT','PENDING') %}<div class="row" style="margin-top:12px">
<form method="post" action="{{ url_for('web.admin_order_status', oid=o['id']) }}" class="grow"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="status" value="PAID"><button class="btn gold block">✅ Verify payment</button></form></div>{% endif %}
<h3 style="margin-top:16px">Change status</h3>
<form method="post" action="{{ url_for('web.admin_order_status', oid=o['id']) }}" class="row" style="flex-wrap:nowrap"><input type="hidden" name="csrf" value="{{ csrf }}">
<select name="status" class="grow">{% for k,v in labels.items() %}<option value="{{ k }}" {{ 'selected' if k==o['status'] }}>{{ v }}</option>{% endfor %}</select><button class="btn">Update</button></form>
<p class="mut small">The customer is notified on Telegram (or sees it in the app).</p>""", o=o, dm=DELIVERY, labels=STATUS_LABEL)


@bp.post("/admin/order/<int:oid>/status")
@admin_required
def admin_order_status(oid):
    o = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    st = request.form.get("status", "")
    if not o or st not in STATUS_LABEL:
        abort(400)
    if st == "AWAITING_PAYMENT":
        db().execute("UPDATE orders SET status=?, payment_notified=0 WHERE id=?", (st, oid))
    elif st == "PAID":
        db().execute("UPDATE orders SET status=?, payment_method='VERIFIED_BY_ADMIN' WHERE id=?", (st, oid))
    else:
        db().execute("UPDATE orders SET status=? WHERE id=?", (st, oid))
    db().commit()
    if o["user_id"] and o["user_id"] > 0:       # Telegram customer: push the update to their chat
        buttons = None
        if st == "DELIVERED":
            buttons = [[{"text": "✅ RECEIVED AND THANK YOU", "callback_data": f"delivery_confirm:{oid}"}]]
        elif st == "OUT_FOR_DELIVERY":
            buttons = [[{"text": "📍 I Received My Order", "callback_data": f"delivery_confirm:{oid}"}]]
        tg_send(o["user_id"], f"📦 Order #AD-{oid:05d} Update\n\n{STATUS_MESSAGES.get(st, st)}\n\nStatus: {STATUS_LABEL[st]}", buttons)
    flash(f"Order updated to {STATUS_LABEL[st]}.")
    return redirect(url_for("web.admin_order", oid=oid))


# ------------------------------------------------------------------ products
def add_photo(pid, fid, label="", first=False):
    d = db()
    n = d.execute("SELECT COUNT(*) FROM product_photos WHERE product_id=?", (pid,)).fetchone()[0]
    legacy = d.execute("SELECT photo_file_id FROM products WHERE id=?", (pid,)).fetchone()
    if n == 0 and legacy and legacy["photo_file_id"] and legacy["photo_file_id"] != fid:
        d.execute("INSERT INTO product_photos(product_id,file_id,label,position) VALUES(?,?,?,0)", (pid, legacy["photo_file_id"], ""))
    if first:
        m = d.execute("SELECT MIN(position) FROM product_photos WHERE product_id=?", (pid,)).fetchone()[0]
        pos = (m if m is not None else 0) - 1
    else:
        m = d.execute("SELECT MAX(position) FROM product_photos WHERE product_id=?", (pid,)).fetchone()[0]
        pos = (m if m is not None else 0) + 1
    d.execute("INSERT INTO product_photos(product_id,file_id,label,position) VALUES(?,?,?,?)", (pid, fid, label[:60], pos))
    sync_cover(pid)
    d.commit()


def sync_cover(pid):
    r = db().execute("SELECT file_id FROM product_photos WHERE product_id=? ORDER BY position,id LIMIT 1", (pid,)).fetchone()
    db().execute("UPDATE products SET photo_file_id=? WHERE id=?", (r["file_id"] if r else None, pid))


@bp.get("/admin/products")
@admin_required
def admin_products():
    q = (request.args.get("q") or "").strip()
    sql, args = "SELECT * FROM products", []
    if q:
        sql += " WHERE name LIKE ?"; args.append(f"%{q}%")
    rows = db().execute(sql + " ORDER BY active DESC, name", args).fetchall()
    return admin_page("Products", """<div class="row between"><h2>Products</h2><a class="btn gold sm" href="{{ url_for('web.admin_product', pid='new') }}">{{ icon('plus') }} Add product</a></div>
<form method="get" class="row" style="flex-wrap:nowrap;margin:8px 0"><input name="q" value="{{ q }}" placeholder="Search products"><button class="btn sm">Search</button></form>
{% for p in rows %}<a class="card li" href="{{ url_for('web.admin_product', pid=p['id']) }}" style="margin-bottom:8px;text-decoration:none;{{ 'opacity:.5' if not p['active'] }}">
{% if p['photo_file_id'] %}<img class="th" src="{{ url_for('web.img', fid=p['photo_file_id']) }}" alt="">{% else %}<div class="th"></div>{% endif %}
<div class="grow"><b>{{ p['name'] }}</b>{% if not p['active'] %} <span class="tag bad">Hidden</span>{% endif %}<div class="small mut">{{ cats.get(p['category'], p['category']) }}</div><div class="price">{{ display_price(p) }}</div></div></a>{% endfor %}
{% if not rows %}<p class="mut">No products.</p>{% endif %}""", rows=rows, q=q, cats=cats())


@bp.route("/admin/product/<pid>", methods=["GET", "POST"])
@admin_required
def admin_product(pid):
    d = db()
    is_new = pid == "new"
    p = None if is_new else d.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not is_new and not p:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:120]
        category = request.form.get("category", "")
        if not name or category not in cats():
            flash("Please enter a name and choose a category.")
            return redirect(request.url)
        sub = request.form.get("subcategory", "")
        if sub not in [k for k, _ in CFG.get("SUBCATEGORIES", {}).get(category, [])]:
            sub = ""
        price_txt = request.form.get("price", "").strip()
        price = to_int(price_txt) if price_txt not in ("", "0") else None
        vals = (name, category, sub, price, request.form.get("description", "").strip(),
                request.form.get("variety", "").strip(), request.form.get("features", "").strip(),
                1 if request.form.get("active") else 0)
        if is_new:
            existing = {r[0] for r in d.execute("SELECT id FROM products")}
            pid = slugify(name, existing)
            d.execute("""INSERT INTO products(id,name,category,subcategory,price,description,variety,features,active)
                         VALUES(?,?,?,?,?,?,?,?,?)""", (pid, *vals))
        else:
            d.execute("""UPDATE products SET name=?,category=?,subcategory=?,price=?,description=?,variety=?,features=?,active=? WHERE id=?""",
                      (*vals, pid))
        # fabric prices (Hijabs / Jilbabs only)
        d.execute("DELETE FROM product_fabric_prices WHERE product_id=?", (pid,))
        if category in color_categories():
            for fk, _ in FABRICS:
                v = to_int(request.form.get(f"fab_{fk}"), 0)
                if v and v > 0:
                    d.execute("INSERT INTO product_fabric_prices VALUES(?,?,?)", (pid, fk, v))
        d.commit()
        # photos
        added = failed = 0
        label = request.form.get("label", "").strip()
        for f in request.files.getlist("photos"):
            if not f or not f.filename:
                continue
            blob = f.read(9_000_000)
            if not (blob[:3] == b"\xff\xd8\xff" or blob[:4] == b"\x89PNG" or (blob[:4] == b"RIFF" and blob[8:12] == b"WEBP")):
                failed += 1; continue
            fid = tg_upload_photo(blob, f"📎 Photo for {name}")
            if fid:
                add_photo(pid, fid, label); added += 1
            else:
                failed += 1
        flash("Product saved." + (f" {added} photo(s) added." if added else "") + (f" {failed} photo(s) failed." if failed else ""))
        return redirect(url_for("web.admin_product", pid=pid))
    photos = [] if is_new else product_photos(pid)
    fp = {} if is_new else fabric_prices(pid)
    return admin_page("Product", """
<a href="{{ url_for('web.admin_products') }}" class="small mut">← All products</a>
<h2>{{ 'Add product' if is_new else 'Edit product' }}</h2>
<form method="post" enctype="multipart/form-data" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Name</label><input name="name" value="{{ p['name'] if p }}" required>
<label class="l">Category</label><select name="category" id="cat" required>{% for k,v in cats.items() %}<option value="{{ k }}" {{ 'selected' if p and p['category']==k }}>{{ v }}</option>{% endfor %}</select>
<label class="l">Type (subcategory)</label><select name="subcategory" id="sub"><option value="">— none —</option>
{% for ck,lst in subs.items() %}{% for sk,sl in lst %}<option value="{{ sk }}" data-cat="{{ ck }}" {{ 'selected' if p and p['category']==ck and p['subcategory']==sk }}>{{ clean(sl) }}</option>{% endfor %}{% endfor %}</select>
<label class="l">Price (₦) — leave empty for "price on request"</label><input name="price" value="{{ p['price'] if p and p['price'] is not none }}" inputmode="numeric">
<div id="fabs" style="display:none"><label class="l">Fabric prices (₦) — Hijabs &amp; Jilbabs only. Customers see the price of the fabric they choose.</label>
{% for k,lab in fabrics %}<div class="row" style="flex-wrap:nowrap;margin-bottom:6px"><span class="grow">{{ lab }}</span><input name="fab_{{ k }}" value="{{ fp.get(k,'') }}" inputmode="numeric" style="max-width:150px" placeholder="not set"></div>{% endfor %}</div>
<label class="l">Description</label><textarea name="description" rows="3">{{ p['description'] if p and p['description'] }}</textarea>
<label class="l">Variety (colours, sizes…)</label><input name="variety" value="{{ p['variety'] if p and p['variety'] }}">
<label class="l">Features</label><input name="features" value="{{ p['features'] if p and p['features'] }}">
<label class="l">Add photos (you can pick many)</label><input type="file" name="photos" accept="image/*" multiple>
<label class="l">Label for these photos (e.g. a colour name) — optional</label><input name="label" placeholder="Royal Blue">
<label class="opt" style="margin-top:12px"><input type="checkbox" name="active" value="1" {{ 'checked' if is_new or p['active'] }}><span>Visible in the shop</span></label>
<p><button class="btn gold block">Save product</button></p></form>
{% if not is_new %}<h3>Gallery ({{ photos|length }})</h3>
<div class="grid">{% for ph in photos %}<div class="card"><img src="{{ url_for('web.img', fid=ph.file_id) }}" style="width:100%;aspect-ratio:1;object-fit:cover;display:block" alt="">
<div class="b" style="padding:8px"><div class="small">{{ ph.label or 'no label' }}{% if loop.first %} · cover{% endif %}</div>
{% if ph.id %}<div class="row" style="margin-top:6px">{% if not loop.first %}<form method="post" action="{{ url_for('web.admin_photo_cover', phid=ph.id) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="pid" value="{{ p['id'] }}"><button class="btn alt sm">Make cover</button></form>{% endif %}
<form method="post" action="{{ url_for('web.admin_photo_delete', phid=ph.id) }}" onsubmit="return confirm('Delete this photo?')"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="pid" value="{{ p['id'] }}"><button class="btn alt sm" style="color:var(--bad)">{{ icon('trash') }}</button></form></div>{% endif %}</div></div>{% endfor %}</div>
<form method="post" action="{{ url_for('web.admin_product_delete', pid=p['id']) }}" onsubmit="return confirm('Hide this product from the shop?')" style="margin-top:20px"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn bad block">Hide product from shop</button></form>{% endif %}
<script>
var cs={{ color_cats|tojson }},ce=document.getElementById('cat'),se=document.getElementById('sub');
function sync(){var c=ce.value;document.getElementById('fabs').style.display=cs.indexOf(c)>=0?'block':'none';
for(var i=0;i<se.options.length;i++){var o=se.options[i];if(!o.dataset.cat)continue;var ok=o.dataset.cat==c;o.hidden=!ok;o.disabled=!ok;if(!ok&&o.selected)se.value='';}}
ce.addEventListener('change',sync);sync();
</script>""", p=p, is_new=is_new, cats=cats(), subs=CFG.get("SUBCATEGORIES", {}), clean=clean_label, photos=photos,
                      fp=fp, fabrics=FABRICS, color_cats=sorted(color_categories()))


@bp.post("/admin/photo/<int:phid>/delete")
@admin_required
def admin_photo_delete(phid):
    pid = request.form.get("pid", "")
    db().execute("DELETE FROM product_photos WHERE id=?", (phid,))
    sync_cover(pid)
    db().commit()
    return redirect(url_for("web.admin_product", pid=pid))


@bp.post("/admin/photo/<int:phid>/cover")
@admin_required
def admin_photo_cover(phid):
    pid = request.form.get("pid", "")
    m = db().execute("SELECT MIN(position) FROM product_photos WHERE product_id=?", (pid,)).fetchone()[0] or 0
    db().execute("UPDATE product_photos SET position=? WHERE id=? AND product_id=?", (m - 1, phid, pid))
    sync_cover(pid)
    db().commit()
    return redirect(url_for("web.admin_product", pid=pid))


@bp.post("/admin/product/<pid>/delete")
@admin_required
def admin_product_delete(pid):
    db().execute("UPDATE products SET active=0 WHERE id=?", (pid,))
    db().commit()
    flash("Product hidden from the shop.")
    return redirect(url_for("web.admin_products"))


# ------------------------------------------------------------------ categories & types
@bp.get("/admin/categories")
@admin_required
def admin_categories():
    rows = db().execute("SELECT * FROM categories ORDER BY position, rowid").fetchall()
    subs = db().execute("SELECT * FROM subcategories ORDER BY position, rowid").fetchall()
    counts = {r["category"]: r["n"] for r in db().execute("SELECT category, COUNT(*) AS n FROM products GROUP BY category")}
    return admin_page("Categories", """<h2>Categories &amp; types</h2>
<form method="post" action="{{ url_for('web.admin_cat_add') }}" class="card row" style="padding:12px;flex-wrap:nowrap"><input type="hidden" name="csrf" value="{{ csrf }}">
<input name="label" placeholder="New category, e.g. 👜 Bags" required class="grow"><button class="btn gold sm">{{ icon('plus') }} Add</button></form>
{% for c in rows %}<div class="card" style="padding:14px;margin-top:12px">
<form method="post" action="{{ url_for('web.admin_cat_update', key=c['key']) }}" class="row" style="flex-wrap:nowrap"><input type="hidden" name="csrf" value="{{ csrf }}">
<input name="label" value="{{ c['label'] }}" class="grow" required><button class="btn sm">Save</button></form>
<label class="opt" style="margin:8px 0 0"><input type="checkbox" form="cf{{ loop.index }}" name="uses_colors" value="1" {{ 'checked' if c['uses_colors'] }} onchange="this.form.submit()"><span class="small">Colour &amp; fabric picker (Hijabs/Jilbabs style)</span></label>
<form id="cf{{ loop.index }}" method="post" action="{{ url_for('web.admin_cat_update', key=c['key']) }}"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="label" value="{{ c['label'] }}"></form>
<div class="small mut" style="margin:8px 0 4px">Types ({{ counts.get(c['key'],0) }} products in this category)</div>
{% for s in subs if s['category']==c['key'] %}<form method="post" action="{{ url_for('web.admin_sub_update', cat=c['key'], key=s['key']) }}" class="row" style="flex-wrap:nowrap;margin-bottom:6px"><input type="hidden" name="csrf" value="{{ csrf }}">
<input name="label" value="{{ s['label'] }}" class="grow" required><button class="btn alt sm">Save</button>
<button class="btn alt sm" style="color:var(--bad)" formaction="{{ url_for('web.admin_sub_delete', cat=c['key'], key=s['key']) }}" formnovalidate onclick="return confirm('Delete this type?')">{{ icon('trash') }}</button></form>{% endfor %}
<form method="post" action="{{ url_for('web.admin_sub_add', cat=c['key']) }}" class="row" style="flex-wrap:nowrap"><input type="hidden" name="csrf" value="{{ csrf }}"><input name="label" placeholder="Add a type, e.g. Handbags" class="grow" required><button class="btn sm">{{ icon('plus') }}</button></form>
<form method="post" action="{{ url_for('web.admin_cat_delete', key=c['key']) }}" onsubmit="return confirm('Delete this category?')" style="margin-top:8px"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn alt sm" style="color:var(--bad)">Delete category</button></form></div>{% endfor %}""",
                      rows=rows, subs=subs, counts=counts)


@bp.post("/admin/categories/add")
@admin_required
def admin_cat_add():
    label = request.form.get("label", "").strip()[:40]
    if label:
        keys = {r[0] for r in db().execute("SELECT key FROM categories")}
        pos = db().execute("SELECT COALESCE(MAX(position),0)+1 FROM categories").fetchone()[0]
        db().execute("INSERT INTO categories(key,label,uses_colors,position) VALUES(?,?,0,?)", (slugify(label, keys)[:20], label, pos))
        db().commit(); reload_cats()
    return redirect(url_for("web.admin_categories"))


@bp.post("/admin/categories/<key>/update")
@admin_required
def admin_cat_update(key):
    label = request.form.get("label", "").strip()[:40]
    if label:
        db().execute("UPDATE categories SET label=?, uses_colors=? WHERE key=?",
                     (label, 1 if request.form.get("uses_colors") else 0, key))
        db().commit(); reload_cats()
    return redirect(url_for("web.admin_categories"))


@bp.post("/admin/categories/<key>/delete")
@admin_required
def admin_cat_delete(key):
    n = db().execute("SELECT COUNT(*) FROM products WHERE category=?", (key,)).fetchone()[0]
    if n:
        flash(f"This category still has {n} product(s). Move or delete them first.")
    else:
        db().execute("DELETE FROM subcategories WHERE category=?", (key,))
        db().execute("DELETE FROM categories WHERE key=?", (key,))
        db().commit(); reload_cats()
    return redirect(url_for("web.admin_categories"))


@bp.post("/admin/categories/<cat>/sub/add")
@admin_required
def admin_sub_add(cat):
    label = request.form.get("label", "").strip()[:40]
    if label and db().execute("SELECT 1 FROM categories WHERE key=?", (cat,)).fetchone():
        keys = {r[0] for r in db().execute("SELECT key FROM subcategories WHERE category=?", (cat,))}
        pos = db().execute("SELECT COALESCE(MAX(position),0)+1 FROM subcategories WHERE category=?", (cat,)).fetchone()[0]
        db().execute("INSERT INTO subcategories(category,key,label,position) VALUES(?,?,?,?)", (cat, slugify(label, keys)[:20], label, pos))
        db().commit(); reload_cats()
    return redirect(url_for("web.admin_categories"))


@bp.post("/admin/categories/<cat>/sub/<key>/update")
@admin_required
def admin_sub_update(cat, key):
    label = request.form.get("label", "").strip()[:40]
    if label:
        db().execute("UPDATE subcategories SET label=? WHERE category=? AND key=?", (label, cat, key))
        db().commit(); reload_cats()
    return redirect(url_for("web.admin_categories"))


@bp.post("/admin/categories/<cat>/sub/<key>/delete")
@admin_required
def admin_sub_delete(cat, key):
    db().execute("UPDATE products SET subcategory='' WHERE category=? AND subcategory=?", (cat, key))
    db().execute("DELETE FROM subcategories WHERE category=? AND key=?", (cat, key))
    db().commit(); reload_cats()
    return redirect(url_for("web.admin_categories"))


# ------------------------------------------------------------------ colour / fabric photo library
@bp.route("/admin/colors", methods=["GET", "POST"])
@admin_required
def admin_colors():
    if request.method == "POST":
        added = 0
        for f in request.files.getlist("photos"):
            blob = f.read(9_000_000) if f and f.filename else b""
            if blob[:3] == b"\xff\xd8\xff" or blob[:4] == b"\x89PNG" or blob[:4] == b"RIFF":
                fid = tg_upload_photo(blob, "📎 Colour/fabric photo")
                if fid:
                    db().execute("INSERT INTO color_options(photo_file_id,active) VALUES(?,1)", (fid,)); added += 1
        db().commit()
        flash(f"{added} colour photo(s) added." if added else "No valid photos were uploaded.")
        return redirect(url_for("web.admin_colors"))
    rows = db().execute("SELECT * FROM color_options WHERE active=1 ORDER BY id").fetchall()
    return admin_page("Colours", """<h2>Colour / fabric photos</h2><p class="mut small">Shown to customers on Hijabs and Jilbabs. Upload as many as you like.</p>
<form method="post" enctype="multipart/form-data" class="card" style="padding:14px"><input type="hidden" name="csrf" value="{{ csrf }}">
<input type="file" name="photos" accept="image/*" multiple required><p style="margin-bottom:0"><button class="btn gold sm">Upload</button></p></form>
<div class="grid" style="margin-top:14px">{% for c in rows %}<div class="card"><img src="{{ url_for('web.img', fid=c['photo_file_id']) }}" style="width:100%;aspect-ratio:1;object-fit:cover;display:block" alt="">
<div class="row between" style="padding:8px"><span class="small">#{{ c['id'] }}</span><form method="post" action="{{ url_for('web.admin_color_delete', cid=c['id']) }}" onsubmit="return confirm('Remove this colour?')"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn alt sm" style="color:var(--bad)">{{ icon('trash') }}</button></form></div></div>{% endfor %}</div>""", rows=rows)


@bp.post("/admin/colors/<int:cid>/delete")
@admin_required
def admin_color_delete(cid):
    db().execute("UPDATE color_options SET active=0 WHERE id=?", (cid,))
    db().commit()
    return redirect(url_for("web.admin_colors"))


# ------------------------------------------------------------------ news
@bp.route("/admin/news", methods=["GET", "POST"])
@admin_required
def admin_news():
    if request.method == "POST":
        title, body = request.form.get("title", "").strip()[:150], request.form.get("body", "").strip()[:4000]
        kind = request.form.get("kind", "news")
        if not title or not body:
            flash("Please add a title and a message.")
            return redirect(url_for("web.admin_news"))
        fid = ""
        blob = read_image_upload("image")
        if blob:
            fid = tg_upload_photo(blob, f"📎 News image: {title}") or ""
            if not fid:
                flash("The picture could not be uploaded, so the post was published without it.")
        db().execute("INSERT INTO news_posts(title,body,kind,pinned,image_file_id) VALUES(?,?,?,?,?)",
                     (title, body, kind if kind in ("news", "giveaway", "offer") else "news",
                      1 if request.form.get("pinned") else 0, fid))
        db().commit()
        flash("Published.")
        return redirect(url_for("web.news"))
    return admin_page("News", """<h2>Post news / giveaway</h2>
<form method="post" enctype="multipart/form-data" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Type</label><select name="kind"><option value="news">News</option><option value="giveaway">Giveaway</option><option value="offer">Offer</option></select>
<label class="l">Title</label><input name="title" required><label class="l">Message</label><textarea name="body" rows="6" required></textarea>
<label class="l">Picture (optional)</label><input type="file" name="image" accept="image/*">
<label class="opt" style="margin-top:12px"><input type="checkbox" name="pinned" value="1"><span>Pin to top</span></label>
<p><button class="btn gold block">Publish</button></p></form>
<p class="mut small">To delete a post or a comment, open it in the News tab and use the trash button.</p>""")


@bp.post("/admin/news/<int:nid>/delete")
@admin_required
def admin_news_delete(nid):
    d = db()
    d.execute("DELETE FROM news_comments WHERE post_id=?", (nid,))
    d.execute("DELETE FROM news_likes WHERE post_id=?", (nid,))
    d.execute("DELETE FROM news_posts WHERE id=?", (nid,))
    d.commit()
    flash("Post deleted.")
    return redirect(url_for("web.news"))


# ------------------------------------------------------------------ settings
@bp.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        set_setting("discount_percent", to_int(request.form.get("discount"), 0, 0, 90))
        for m in DELIVERY:
            set_setting(f"delivery_fee_{m}", to_int(request.form.get(f"fee_{m}"), 0, 0))
        set_setting("accepting_orders", "1" if request.form.get("accepting") else "0")
        set_setting("welcome_message", request.form.get("welcome", "").strip()[:700])
        for field, key in (("logo", "web_logo"), ("flyer", "web_flyer")):
            blob = read_image_upload(field)
            if blob:
                fid = tg_upload_photo(blob, f"📎 App {field}")
                if fid:
                    set_setting(key, fid)
                else:
                    flash(f"The {field} could not be uploaded. Try again.")
        if request.form.get("remove_flyer"):
            set_setting("web_flyer", "")
        flash("Settings saved.")
        return redirect(url_for("web.admin_settings"))
    return admin_page("Settings", """<h2>Settings</h2>
<form method="post" enctype="multipart/form-data" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="opt"><input type="checkbox" name="accepting" value="1" {{ 'checked' if accepting }}><span>Accepting new orders</span></label>
<label class="l">Store-wide discount (%) — 0 switches it off</label><input name="discount" value="{{ discount }}" inputmode="numeric">
<label class="l">Delivery fees (₦, 0 = free / confirmed later)</label>
{% for k,v in dm.items() %}<div class="row" style="flex-wrap:nowrap;margin-bottom:6px"><span class="grow small">{{ v }}</span><input name="fee_{{ k }}" value="{{ fees[k] }}" inputmode="numeric" style="max-width:130px"></div>{% endfor %}
<label class="l">Telegram welcome message (the shop name and tagline are added automatically)</label><textarea name="welcome" rows="4">{{ welcome }}</textarea>
<label class="l">App logo — becomes the app icon and the opening screen (square works best)</label><input type="file" name="logo" accept="image/*">
<label class="l">Flyer — shown on the Create account / Log in page (AD FASHION HIJABS AND MORE)</label><input type="file" name="flyer" accept="image/*">
{% if has_flyer %}<label class="opt" style="margin-top:8px"><input type="checkbox" name="remove_flyer" value="1"><span>Remove current flyer</span></label>{% endif %}
<p><button class="btn gold block">Save settings</button></p></form>
<p class="mut small">After changing the logo, remove the app from the phone and install it again to refresh the home-screen icon.</p>""",
                      accepting=setting("accepting_orders", "1") != "0", discount=discount_pct(), dm=DELIVERY,
                      fees={k: delivery_fee(k) for k in DELIVERY}, welcome=setting("welcome_message"),
                      has_flyer=bool(setting("web_flyer")))


# ------------------------------------------------------------------ customers
@bp.get("/admin/customers")
@admin_required
def admin_customers():
    rows = db().execute("""SELECT u.*, (SELECT COUNT(*) FROM orders o WHERE o.web_user_id=u.id) AS n
                           FROM web_users u ORDER BY u.id DESC LIMIT 300""").fetchall()
    return admin_page("Customers", """<h2>App customers ({{ rows|length }})</h2>
{% for u in rows %}<div class="card" style="padding:12px;margin-bottom:8px"><div class="row between"><b>{{ u['name'] }}</b><span class="tag g">{{ u['score'] }} pts</span></div>
<div class="small mut">{{ u['email'] }}{% if u['phone'] %} · {{ u['phone'] }}{% endif %} · {{ u['n'] }} order(s) · joined {{ u['created_at'][:10] }}</div>
<form method="post" action="{{ url_for('web.admin_points', uid=u['id']) }}" class="row" style="flex-wrap:nowrap;margin-top:8px"><input type="hidden" name="csrf" value="{{ csrf }}">
<input name="delta" placeholder="± points" inputmode="numeric" style="max-width:110px"><button class="btn alt sm">Adjust points</button></form></div>{% endfor %}""", rows=rows)


@bp.post("/admin/customers/<int:uid>/points")
@admin_required
def admin_points(uid):
    delta = to_int(request.form.get("delta"), 0)
    if delta:
        db().execute("UPDATE web_users SET score=MAX(0, score+?) WHERE id=?", (delta, uid))
        db().commit()
    return redirect(url_for("web.admin_customers"))


@bp.errorhandler(403)
def forbidden(_e):
    return page("Not allowed", "<h2>Not allowed</h2><p class='mut'>You don't have access to this page.</p>"), 403


@bp.errorhandler(404)
def not_found(_e):
    return page("Not found", "<h2>Page not found</h2><p><a class='btn gold' href='/'>Go home</a></p>"), 404


# ================================================================== registration into the bot's Flask app
def register(flask_app, cfg):
    from datetime import timedelta
    from werkzeug.middleware.proxy_fix import ProxyFix
    CFG.update(cfg)
    init_web_db()
    # A stable secret key (kept in the database volume) so people stay signed in across redeploys.
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        con = sqlite3.connect(CFG["DB"])
        row = con.execute("SELECT value FROM settings WHERE key='web_secret'").fetchone()
        if row and row[0]:
            secret = row[0]
        else:
            secret = secrets.token_hex(32)
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('web_secret',?)", (secret,))
            con.commit()
        con.close()
    flask_app.secret_key = secret
    flask_app.wsgi_app = ProxyFix(flask_app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    flask_app.config.update(
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("SITE_URL")),
        PERMANENT_SESSION_LIFETIME=timedelta(days=365), MAX_CONTENT_LENGTH=40 * 1024 * 1024)
    flask_app.register_blueprint(bp)
