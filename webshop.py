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
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint, abort, flash, g, has_request_context, redirect, render_template_string,
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
# Textiles are sold per yard or per trouser (each with its own price); quantity may be a decimal (2.5 yards).
UNITS = [("yd", "Per yard"), ("tr", "Per trouser")]
UNIT_WORD = {"yd": "yard", "tr": "trouser"}
ALL_PRICE_KEYS = FABRICS + UNITS
FABRIC_NAME = dict(ALL_PRICE_KEYS)


def is_unit_key(k):
    return k in UNIT_WORD


def fmt_qty(q):
    try:
        return f"{float(q):g}"
    except (TypeError, ValueError):
        return str(q)


def line_total(price, qty):
    return int(round((price or 0) * qty))


def parse_qty(raw, decimal=False, lo=1, hi=500):
    """Typed quantity -> number or None. Decimals are rounded to the nearest quarter (2.5, 2.25)."""
    t = str(raw if raw is not None else "").strip().replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    if v != v or v < lo or v > hi:
        return None
    if decimal:
        v = round(v * 4) / 4
        if v < lo:
            return None
        return int(v) if v == int(v) else v
    return int(v)
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
PHONE_EXEMPT = {"web.complete_profile", "web.logout", "web.icon_maskable", "web.push_key", "web.admin_lock_route"}
PUBLIC_ENDPOINTS = {
    "web.home", "web.login", "web.register_page", "web.manifest", "web.service_worker",
    "web.google_auth", "web.pubimg", "web.pubvid", "web.qr_png", "web.qr_card", "web.icon", "web.brand_logo", "web.brand_flyer", "web.health", "web.robots",
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
    "share": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>',
    "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>',
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
        CREATE TABLE IF NOT EXISTS push_subs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL,
            body TEXT DEFAULT '', url TEXT DEFAULT '/', is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS social_posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, platform TEXT, status TEXT,
            remote_id TEXT DEFAULT '', error TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS share_jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT DEFAULT '', kind TEXT, want TEXT DEFAULT '[]',
            picked TEXT DEFAULT '[]', caps TEXT DEFAULT '{}', take INTEGER DEFAULT 0, status TEXT DEFAULT 'queued',
            result TEXT DEFAULT '[]', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, finished_at TIMESTAMP);
        CREATE TABLE IF NOT EXISTS wa_sent(user_id INTEGER PRIMARY KEY, sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);
    """)

    def addcol(table, col, ddl):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if cols and col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    addcol("orders", "web_user_id", "INTEGER")
    addcol("orders", "received_confirmed", "INTEGER DEFAULT 0")
    addcol("social_posts", "caption", "TEXT DEFAULT ''")
    addcol("social_posts", "auto", "INTEGER DEFAULT 0")
    addcol("orders", "points_awarded", "INTEGER DEFAULT 0")
    if c.execute("PRAGMA table_info(products)").fetchall() and "added_at" not in {r[1] for r in c.execute("PRAGMA table_info(products)")}:
        c.execute("ALTER TABLE products ADD COLUMN added_at TEXT")
        # first run only: the 8 most recently added products count as new arrivals so the section is not empty
        c.execute("UPDATE products SET added_at=datetime('now') WHERE rowid IN (SELECT rowid FROM products WHERE active=1 ORDER BY rowid DESC LIMIT 8)")
    if c.execute("PRAGMA table_info(products)").fetchall():
        # every product added from now on (Telegram bot or app) is stamped with its upload date
        c.execute("""CREATE TRIGGER IF NOT EXISTS trg_products_added AFTER INSERT ON products
                     BEGIN UPDATE products SET added_at=datetime('now') WHERE rowid=NEW.rowid; END""")
    addcol("order_items", "fabric", "TEXT DEFAULT ''")
    addcol("news_posts", "image_file_id", "TEXT DEFAULT ''")
    for uid, ph in c.execute("SELECT id, phone FROM web_users").fetchall():
        good = norm_phone(ph)
        if good and good != ph:
            c.execute("UPDATE web_users SET phone=? WHERE id=?", (good, uid))
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


def norm_phone(raw):
    """One canonical form (+234...) so the same number is never saved twice. '' = not a valid number."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0") and len(d) == 11:
        d = "234" + d[1:]
    elif len(d) == 10 and d[0] in "789":
        d = "234" + d
    return "+" + d if 8 <= len(d) <= 15 else ""


def points_for(total):
    """Style points earned by an order: 1 point per N1,000 spent (at least 1)."""
    try:
        return max(1, int(total) // 1000)
    except (TypeError, ValueError):
        return 1


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


ADMIN_IDLE = 600          # seconds of silence after which the admin PIN is asked again


def _admin_email():
    u = current_user()
    return u["email"].strip().lower() if u else ""


def admin_pin_set():
    return bool(setting("admin_pin:" + _admin_email()))


def admin_unlocked():
    """True only while this session has entered the 6-digit PIN recently (and the app has not been reopened)."""
    if not session.get("admin_ok") or session.get("admin_for") != _admin_email():
        return False
    return time.time() - session.get("admin_seen", 0) < ADMIN_IDLE


def admin_lock():
    for k in ("admin_ok", "admin_for", "admin_seen"):
        session.pop(k, None)


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user():
            return redirect(url_for("web.login", next=request.path))
        if not is_admin_user():
            abort(403)
        if not admin_unlocked():
            admin_lock()
            if request.method != "GET":
                abort(403)
            return redirect(url_for("web.admin_pin", next=request.full_path.rstrip("?")))
        session["admin_seen"] = time.time()
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
        if not secrets.compare_digest(request.form.get("csrf", "") or request.headers.get("X-CSRF", ""), session.get("csrf", "x")):
            abort(400, "Session expired. Go back and try again.")
    # Login wall: everything except the welcome/login/register pages and app assets
    if request.endpoint not in PUBLIC_ENDPOINTS and not current_user():
        if request.method == "GET":
            return redirect(url_for("web.home"))
        abort(403)
    # Every customer must have a phone number on file (older accounts and Google sign-ins are asked once)
    u = current_user()
    if u and not norm_phone(u["phone"]) and request.endpoint not in PHONE_EXEMPT and request.endpoint not in PUBLIC_ENDPOINTS:
        if request.method == "GET":
            return redirect(url_for("web.complete_profile", next=request.path))
        abort(403)


def start_session(user_id, keep=None):
    session.clear()
    session.permanent = True        # stays signed in until the user signs out
    session["uid"] = user_id
    session["csrf"] = secrets.token_hex(16)
    if keep:
        session["cart"] = keep


# ------------------------------------------------------------------ catalog helpers
NEW_DAYS = 30


def new_arrivals(limit=None):
    """Products uploaded in the last 30 days, newest first."""
    sql = ("SELECT *, 1 AS is_new FROM products WHERE active=1 AND added_at IS NOT NULL "
           f"AND added_at >= datetime('now','-{NEW_DAYS} days') ORDER BY added_at DESC, rowid DESC")
    return db().execute(sql + (f" LIMIT {int(limit)}" if limit else "")).fetchall()


def products(category=None, q=None, sub=None):
    sql, args = ("SELECT *, (added_at IS NOT NULL AND added_at >= datetime('now','-%d days')) AS is_new "
                 "FROM products WHERE active=1" % NEW_DAYS), []
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
    return {k: have[k] for k, _ in ALL_PRICE_KEYS if k in have}


def unit_price(p, fab=None):
    fp = fabric_prices(p["id"])
    return fp[fab] if fab and fab in fp else p["price"]


def display_price(p):
    fp = fabric_prices(p["id"])
    units = {k: v for k, v in fp.items() if is_unit_key(k)}
    if units:
        return " · ".join(f"{money(v)}/{UNIT_WORD[k]}" for k, v in units.items())
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
        line = line_total(price, qty)
        total += line
        items.append({"key": key, "p": p, "qty": qty, "line": line, "color": color,
                      "fab": fab, "fab_name": FABRIC_NAME.get(fab, ""), "price": price,
                      "unit": UNIT_WORD.get(fab, ""), "step": 0.5 if is_unit_key(fab) else 1})
    return items, total


# ------------------------------------------------------------------ QR code (pure Python, no extra package)
_QR_EC = {
    "M": {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
          6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
          10: (26, [(4, 43), (1, 44)])},
    "H": {1: (17, [(1, 9)]), 2: (28, [(1, 16)]), 3: (22, [(2, 13)]), 4: (16, [(4, 9)]), 5: (22, [(2, 11), (2, 12)]),
          6: (28, [(4, 15)]), 7: (26, [(4, 13), (1, 14)]), 8: (26, [(4, 14), (2, 15)]), 9: (24, [(4, 12), (4, 13)]),
          10: (28, [(6, 15), (2, 16)])},
}
_QR_FMT = {"M": 0, "H": 2}
_QR_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
             9: [6, 26, 46], 10: [6, 28, 50]}


def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_gen(deg):
    res = [0] * (deg - 1) + [1]
    root = 1
    for _ in range(deg):
        for j in range(deg):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < deg:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 2)
    return res


def _rs_rem(data, gen):
    res = [0] * len(gen)
    for b in data:
        f = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(gen):
            res[i] ^= _gf_mul(c, f)
    return res


def qr_matrix(text, level="H"):
    """Returns the QR code as a list of rows of booleans (True = dark). Byte mode, versions 1-10."""
    raw = text.encode("utf-8")
    table = _QR_EC[level]
    ver = None
    for v in range(1, 11):
        ec, groups = table[v]
        cap = sum(c * d for c, d in groups) * 8
        if cap >= 4 + (8 if v < 10 else 16) + 8 * len(raw):
            ver = v
            break
    if ver is None:
        raise ValueError("Text too long for the QR code")
    ecn, groups = table[ver]
    total_data = sum(c * d for c, d in groups)
    bits = [0, 1, 0, 0]
    cc = 8 if ver < 10 else 16
    bits += [(len(raw) >> i) & 1 for i in range(cc - 1, -1, -1)]
    for b in raw:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, total_data * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < total_data:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    blocks, k = [], 0
    for cnt, dl in groups:
        for _ in range(cnt):
            blocks.append(data[k:k + dl])
            k += dl
    gen = _rs_gen(ecn)
    eccs = [_rs_rem(b, gen) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ecn):
        for e in eccs:
            out.append(e[i])
    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = bool(dark)
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)

    def finder(cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                xx, yy = cx + dx, cy + dy
                if 0 <= xx < size and 0 <= yy < size:
                    setf(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
    finder(3, 3); finder(size - 4, 3); finder(3, size - 4)
    pos = _QR_ALIGN[ver]
    n = len(pos)
    for i in range(n):
        for j in range(n):
            if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)

    def fmt_bits(mask):
        d = (_QR_FMT[level] << 3) | mask
        r = d
        for _ in range(10):
            r = (r << 1) ^ ((r >> 9) * 0x537)
        b = ((d << 10) | r) ^ 0x5412
        bit = lambda i: (b >> i) & 1
        for i in range(0, 6):
            setf(8, i, bit(i))
        setf(8, 7, bit(6)); setf(8, 8, bit(7)); setf(7, 8, bit(8))
        for i in range(9, 15):
            setf(14 - i, 8, bit(i))
        for i in range(0, 8):
            setf(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, bit(i))
        setf(8, size - 8, True)
    fmt_bits(0)
    if ver >= 7:
        r = ver
        for _ in range(12):
            r = (r << 1) ^ ((r >> 11) * 0x1F25)
        b = (ver << 12) | r
        for i in range(18):
            a, c = size - 11 + i % 3, i // 3
            setf(a, c, (b >> i) & 1)
            setf(c, a, (b >> i) & 1)
    # place codewords (zig-zag)
    i = 0
    for right in range(size - 1, 0, -2):
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = (size - 1 - vert) if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < len(out) * 8:
                    mod[y][x] = bool((out[i >> 3] >> (7 - (i & 7))) & 1)
                    i += 1
    conds = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]

    def penalty(m):
        p = 0
        for grid in (m, [list(r) for r in zip(*m)]):
            for row in grid:
                run = 1
                for k in range(1, size):
                    if row[k] == row[k - 1]:
                        run += 1
                    else:
                        if run >= 5:
                            p += run - 2
                        run = 1
                if run >= 5:
                    p += run - 2
                s = "".join("1" if v else "0" for v in row)
                p += 40 * (s.count("10111010000") + s.count("00001011101"))
        for y in range(size - 1):
            for x in range(size - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    p += 3
        dark = sum(v for r in m for v in r)
        p += 10 * (abs(dark * 20 - size * size * 10) // (size * size))
        return p
    best, best_p = None, None
    for mask in range(8):
        m = [[mod[y][x] ^ (conds[mask](x, y) and not fn[y][x]) for x in range(size)] for y in range(size)]
        pp = penalty(m)
        if best is None or pp < best_p:
            best, best_p, best_mask = m, pp, mask
    mod = best
    fmt_bits(best_mask)          # writes format modules into `mod`
    return mod


def qr_image(text, logo=None, scale=12, border=4):
    """QR code as a Pillow image. Error correction level H, so a logo can cover the middle without breaking scans."""
    from PIL import Image, ImageDraw
    m = qr_matrix(text, "H")
    n = len(m)
    px = (n + 2 * border) * scale
    im = Image.new("RGB", (px, px), "white")
    d = ImageDraw.Draw(im)
    for y in range(n):
        for x in range(n):
            if m[y][x]:
                X, Y = (x + border) * scale, (y + border) * scale
                d.rectangle([X, Y, X + scale - 1, Y + scale - 1], fill="black")
    if logo is not None:
        qr_px = n * scale
        dia = int(qr_px * 0.22)                      # logo covers about 4% of the code: safe with level H
        pad = max(4, dia // 9)
        w, h = logo.size
        side = min(w, h)
        sq = logo.convert("RGB").crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize((dia, dia))
        mask = Image.new("L", (dia * 4, dia * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, dia * 4 - 1, dia * 4 - 1], fill=255)
        mask = mask.resize((dia, dia), Image.LANCZOS)
        back = dia + 2 * pad
        cx = px // 2
        ring = Image.new("L", (back * 4, back * 4), 0)
        ImageDraw.Draw(ring).ellipse([0, 0, back * 4 - 1, back * 4 - 1], fill=255)
        ring = ring.resize((back, back), Image.LANCZOS)
        im.paste(Image.new("RGB", (back, back), "white"), (cx - back // 2, cx - back // 2), ring)
        im.paste(sq, (cx - dia // 2, cx - dia // 2), mask)
    return im


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


# ------------------------------------------------------------------ web push (phone notifications from the app itself)
import base64


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(t):
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))


def _raw_setting(c, key):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def vapid_keys(c):
    """(private_raw_b64u, public_b64u). Created once and kept in the database so subscriptions never break."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    priv = _raw_setting(c, "vapid_private")
    pub = _raw_setting(c, "vapid_public")
    if not priv or not pub:
        k = ec.generate_private_key(ec.SECP256R1())
        priv = _b64u(k.private_numbers().private_value.to_bytes(32, "big"))
        pub = _b64u(k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        for key, val in (("vapid_private", priv), ("vapid_public", pub)):
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, val))
        c.commit()
    return priv, pub


def _encrypt_payload(payload, p256dh, auth):
    """RFC 8291 / 8188 aes128gcm encryption (used when the pywebpush library is not installed)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub_b = _b64d(p256dh)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_b)
    as_priv = ec.generate_private_key(ec.SECP256R1())
    as_pub_b = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    secret = as_priv.exchange(ec.ECDH(), ua_pub)
    salt = os.urandom(16)
    ikm = HKDF(hashes.SHA256(), 32, _b64d(auth), b"WebPush: info\0" + ua_pub_b + as_pub_b).derive(secret)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\0").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\0").derive(ikm)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub_b)]) + as_pub_b + ct


def _vapid_auth(endpoint, priv_b64u, pub_b64u, subject):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urllib.parse.urlparse(endpoint)
    head = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject},
                              separators=(",", ":")).encode())
    key = ec.derive_private_key(int.from_bytes(_b64d(priv_b64u), "big"), ec.SECP256R1())
    r, sg = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    jwt = f"{head}.{claims}.{_b64u(r.to_bytes(32, 'big') + sg.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={pub_b64u}"


def _send_one(sub, payload, priv, pub, subject):
    """Returns 'ok', 'gone' (subscription dead, delete it) or 'err'."""
    endpoint, p256dh, auth = sub
    try:
        try:
            from pywebpush import webpush, WebPushException
            try:
                webpush({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}, data=payload,
                        vapid_private_key=priv, vapid_claims={"sub": subject}, ttl=86400, timeout=15)
                return "ok"
            except WebPushException as e:
                code = getattr(getattr(e, "response", None), "status_code", 0)
                return "gone" if code in (404, 410) else "err"
        except ImportError:
            body = _encrypt_payload(payload.encode(), p256dh, auth)
            req = urllib.request.Request(endpoint, data=body, method="POST", headers={
                "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": "86400",
                "Urgency": "high", "Authorization": _vapid_auth(endpoint, priv, pub, subject)})
            urllib.request.urlopen(req, timeout=15).read()
            return "ok"
    except urllib.error.HTTPError as e:
        return "gone" if e.code in (404, 410) else "err"
    except Exception:
        return "err"


def _push_to(user_ids=None, admins=False, title="", body="", url="/", tag=""):
    """Save the message in each person's in-app inbox (bell icon) and send a phone notification.
    Runs in the background so it never slows or breaks a request."""
    def run():
        try:
            c = sqlite3.connect(CFG["DB"])
            ids = set(user_ids or [])
            if admins:
                mails = admin_emails()
                for r in c.execute("SELECT id,email FROM web_users"):
                    if r[1].strip().lower() in mails:
                        ids.add(r[0])
            if not ids:
                c.close()
                return
            c.executemany("INSERT INTO notifications(user_id,title,body,url) VALUES(?,?,?,?)",
                          [(i, title, body, url) for i in ids])
            c.commit()
            priv, pub = vapid_keys(c)
            q = ",".join("?" * len(ids))
            subs = c.execute(f"SELECT endpoint,p256dh,auth FROM push_subs WHERE user_id IN ({q})", list(ids)).fetchall()
            payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
            subject = "mailto:" + (next(iter(admin_emails()), "") or "admin@example.com")
            for sub in subs:
                if _send_one(tuple(sub), payload, priv, pub, subject) == "gone":
                    c.execute("DELETE FROM push_subs WHERE endpoint=?", (sub[0],))
                    c.commit()
            c.close()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def push_admins(title, body, url="/admin", tag=""):
    _push_to(admins=True, title=title, body=body, url=url, tag=tag)


def push_users(user_ids, title, body, url="/", tag=""):
    if user_ids:
        _push_to(user_ids=list(user_ids), title=title, body=body, url=url, tag=tag)


def push_all_customers(title, body, url="/news"):
    try:
        c = sqlite3.connect(CFG["DB"])
        ids = [r[0] for r in c.execute("SELECT id FROM web_users")]
        c.close()
    except Exception:
        ids = []
    push_users(ids, title, body, url, tag="news")


def _award_points(oid):
    """Give the customer style points for a delivered order, once. Returns the points (0 if none/already given)."""
    c = sqlite3.connect(CFG["DB"])
    try:
        r = c.execute("SELECT web_user_id,total,points_awarded FROM orders WHERE id=?", (oid,)).fetchone()
        if not r or not r[0] or r[2]:
            return 0
        pts = points_for(r[1])
        c.execute("UPDATE web_users SET score=score+? WHERE id=?", (pts, r[0]))
        c.execute("UPDATE orders SET points_awarded=? WHERE id=?", (pts, oid))
        c.commit()
        return pts
    finally:
        c.close()


def push_order_status(oid, status):
    """Called by both the web admin and the Telegram bot whenever an order status changes:
    awards style points on delivery and tells the app customer."""
    pts = 0
    try:
        if status == "DELIVERED":
            pts = _award_points(oid)
        c = sqlite3.connect(CFG["DB"])
        r = c.execute("SELECT web_user_id FROM orders WHERE id=?", (oid,)).fetchone()
        c.close()
    except Exception:
        return
    if not r or not r[0]:
        return
    msg = STATUS_MESSAGES.get(status, STATUS_LABEL.get(status, status))
    if status == "DELIVERED":
        msg += " Tap here and press RECEIVED AND THANK YOU."
        if pts:
            msg += f" You earned {pts} style points!"
    push_users([r[0]], f"Order #AD-{oid:05d}: {STATUS_LABEL.get(status, status)}", msg, f"/order/{oid}", tag=f"order{oid}")


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


def _make_icon(src, size, scale):
    """Whole logo, never cropped, centred on a square canvas matching its edge colour."""
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    flat = Image.new("RGBA", im.size, (17, 17, 17, 255))
    flat.alpha_composite(im)
    im = flat.convert("RGB")
    w, h = im.size
    px = im.load()
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    bg = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
    inner = max(1, int(size * scale))
    ratio = min(inner / w, inner / h)
    im = im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))
    canvas = Image.new("RGB", (size, size), bg)
    canvas.paste(im, ((size - im.size[0]) // 2, (size - im.size[1]) // 2))
    buf = io.BytesIO(); canvas.save(buf, "PNG")
    return buf.getvalue()


def _icon_response(size, scale, tag):
    fid = setting("web_logo")
    src = fetch_file(fid) if fid else None
    out = os.path.join(_cache_dir(), f"icon{tag}_{hashlib.sha256((fid or 'default').encode()).hexdigest()[:12]}_{size}.png")
    if not os.path.exists(out):
        data = None
        if src:
            try:
                data = _make_icon(src, size, scale)
            except Exception:
                data = None
        if data is None and src:
            with open(src, "rb") as f:      # no Pillow available: use the logo as it is
                data = f.read()
        if data is None:
            data = _default_logo_png(size)
        with open(out, "wb") as f:
            f.write(data)
    return send_file(out, mimetype="image/png", max_age=3600)


@bp.get("/icon-<int:size>.png")
def icon(size):
    if size not in (192, 512):
        abort(404)
    return _icon_response(size, 1.0, "")


@bp.get("/icon-maskable-<int:size>.png")
def icon_maskable(size):
    if size not in (192, 512):
        abort(404)
    return _icon_response(size, 0.68, "m")


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
.brand img{width:44px;height:44px;border-radius:50%;object-fit:contain;border:2px solid var(--gold);background:#000;flex:none}
.brand b{font:700 clamp(15px,4.6vw,19px) 'Cormorant Garamond',serif;color:#e6c36b;letter-spacing:.8px;white-space:nowrap}
.ib{position:relative;color:#fff;padding:6px;display:block;text-decoration:none}
.pc{position:relative}.nb{position:absolute;top:8px;left:8px;z-index:2;background:var(--gold);color:#111;font:700 10px Inter;letter-spacing:.6px;padding:3px 8px;border-radius:99px}
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
<a class="ib" href="{{ url_for('web.notifications') }}" aria-label="Notifications">{{ icon('bell') }}{% if unread %}<span class="badge">{{ unread if unread<100 else '99+' }}</span>{% endif %}</a>
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
<div id="pushbar" style="display:none;position:fixed;left:12px;right:12px;bottom:90px;background:#111;color:#e6c36b;padding:12px 16px;border-radius:14px;z-index:41;align-items:center;gap:12px;border:1px solid var(--gold)"><span style="flex:1;font-size:14px">🔔 Get updates about your orders, offers and news</span><button class="btn gold sm" id="pushon">Turn on</button><button id="pushx" style="border:0;background:none;color:#e6c36b;font-size:18px">✕</button></div>
<div id="inst"><span style="flex:1;font-size:14px">Install <b>{{ app_name }}</b> on your phone</span>
<button class="btn gold sm" id="instbtn">Install</button><button id="instx" style="border:0;background:none;color:#e6c36b;font-size:18px">✕</button></div>
<script>
(function(){var s=document.getElementById('splash');var seen=false;try{seen=sessionStorage.getItem('splashed')}catch(e){}
function hide(){s.style.opacity=0;setTimeout(function(){s.style.display='none'},500);try{sessionStorage.setItem('splashed','1')}catch(e){}}
if(seen){s.style.display='none'}else{window.addEventListener('load',function(){setTimeout(hide,1100)});setTimeout(hide,3500)}})();
{% if is_admin %}try{if(!sessionStorage.getItem('adm_open')){sessionStorage.setItem('adm_open','1');var fd=new FormData();fd.append('csrf','{{ csrf }}');fetch('/admin/lock',{method:'POST',body:fd,credentials:'same-origin'});}}catch(e){}{% endif %}
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){});}
{% if user %}(function(){
var CS='{{ csrf }}';
function u8(b){var p='='.repeat((4-b.length%4)%4),s=(b+p).replace(/-/g,'+').replace(/_/g,'/'),r=atob(s),o=new Uint8Array(r.length);for(var i=0;i<r.length;i++)o[i]=r.charCodeAt(i);return o;}
function post(path,body){return fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF':CS},body:JSON.stringify(body)});}
function sub(){return navigator.serviceWorker.ready.then(function(reg){return reg.pushManager.getSubscription().then(function(s){if(s)return s;return fetch('/push/key').then(function(r){return r.json()}).then(function(k){return reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:u8(k.key)});});});}).then(function(s){return post('/push/subscribe',s.toJSON());});}
var ok=('serviceWorker' in navigator)&&('PushManager' in window)&&('Notification' in window);
if(ok){
 var bar=document.getElementById('pushbar');var dis=false;try{dis=localStorage.getItem('pushx')}catch(_){}
 if(Notification.permission==='granted'){sub().catch(function(){});}
 else if(Notification.permission==='default'&&!dis){setTimeout(function(){bar.style.display='flex';},2500);}
 document.getElementById('pushon').onclick=function(){Notification.requestPermission().then(function(p){bar.style.display='none';if(p==='granted')sub().catch(function(){});});};
 document.getElementById('pushx').onclick=function(){bar.style.display='none';try{localStorage.setItem('pushx','1')}catch(_){}};
 document.addEventListener('submit',function(e){var f=e.target;if(f.getAttribute('action')&&f.getAttribute('action').indexOf('/logout')>-1&&!f.dataset.done){e.preventDefault();f.dataset.done='1';
  navigator.serviceWorker.ready.then(function(r){return r.pushManager.getSubscription()}).then(function(s){return s?post('/push/unsubscribe',{endpoint:s.endpoint}).then(function(){return s.unsubscribe()}):0}).catch(function(){}).then(function(){f.submit();});}});
}
})();{% endif %}
var dp=null;window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();dp=e;var b=document.getElementById('inst');var x=false;try{x=sessionStorage.getItem('instx')}catch(_){}if(!x)b.style.display='flex';});
document.getElementById('instbtn').onclick=function(){if(dp){dp.prompt();dp=null;}document.getElementById('inst').style.display='none';};
document.getElementById('instx').onclick=function(){document.getElementById('inst').style.display='none';try{sessionStorage.setItem('instx','1')}catch(e){}};
</script></body></html>"""

PCARD = """{% macro pcard(p) %}<a class="card pc" href="{{ url_for('web.product', pid=p['id']) }}">{% if p['is_new'] %}<span class="nb">NEW</span>{% endif %}
{% if p['photo_file_id'] %}<img loading="lazy" src="{{ url_for('web.img', fid=p['photo_file_id']) }}" alt="{{ p['name'] }}">{% else %}<img alt="">{% endif %}
<div class="b"><div class="t">{{ p['name'] }}</div><div class="price">{{ display_price(p) }}</div></div></a>{% endmacro %}"""


def unread_count():
    u = current_user()
    if not u:
        return 0
    return db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (u["id"],)).fetchone()[0]


def page(title, body_tpl, tab="", **ctx):
    common = dict(csrf=csrf_token(), money=money, cfg=CFG, user=current_user(), status_label=STATUS_LABEL,
                  icon=svg_icon, display_price=display_price, app_name=APP_NAME, is_admin=is_admin_user(),
                  fabric_name=FABRIC_NAME, fq=fmt_qty, unit_word=UNIT_WORD)
    body = render_template_string(PCARD + body_tpl, **common, **ctx)
    return render_template_string(BASE, title=title, body=body, tab=tab, cart_count=sum(cart().values()),
                                  unread=unread_count(), **{k: common[k] for k in ("user", "icon", "app_name", "is_admin", "csrf")})


def safe_next(default):
    n = request.form.get("next") or request.args.get("next") or ""
    return n if n.startswith("/") and not n.startswith("//") else default


# ================================================================== PWA files
@bp.get("/manifest.webmanifest")
def manifest():
    v = hashlib.sha256((setting("web_logo") or "default").encode()).hexdigest()[:8]
    data = {
        "name": APP_NAME, "short_name": APP_NAME, "id": "/", "start_url": "/", "scope": "/",
        "display": "standalone", "orientation": "portrait", "background_color": "#111111", "theme_color": "#111111",
        "description": "AD FASHION HIJABS AND MORE - hijabs, jilbabs, textiles and more.",
        "icons": [
            {"src": f"/icon-192.png?v={v}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"/icon-512.png?v={v}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"/icon-maskable-512.png?v={v}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json")


@bp.get("/notifications")
@login_required
def notifications():
    uid = current_user()["id"]
    rows = db().execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (uid,)).fetchall()
    unread_ids = [r["id"] for r in rows if not r["is_read"]]
    resp = page("Notifications", """
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px"><h2 style="margin:0">Notifications</h2>
{% if rows %}<form method="post" action="{{ url_for('web.notifications_clear') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn sm">Clear all</button></form>{% endif %}</div>
{% for n in rows %}<a class="card" href="{{ url_for('web.notification_open', nid=n['id']) }}" style="display:block;padding:12px 14px;margin-bottom:8px;text-decoration:none;color:inherit;{{ 'border-left:4px solid var(--gold);' if not n['is_read'] }}">
<div style="font-weight:{{ 600 if not n['is_read'] else 500 }}">{{ n['title'] }}</div>
{% if n['body'] %}<div class="mut small" style="margin-top:2px">{{ n['body'] }}</div>{% endif %}
<div class="mut small" style="margin-top:4px;font-size:11px">{{ n['created_at'] }}</div></a>
{% else %}<div class="card" style="padding:28px;text-align:center"><div style="font-size:34px">🔔</div><p class="mut">No notifications yet. Order updates, offers and news will appear here.</p></div>{% endfor %}""",
        tab="", rows=rows)
    if unread_ids:      # opening the list marks everything as seen
        q = ",".join("?" * len(unread_ids))
        db().execute(f"UPDATE notifications SET is_read=1 WHERE id IN ({q})", unread_ids)
        db().commit()
    return resp


@bp.get("/notifications/<int:nid>")
@login_required
def notification_open(nid):
    uid = current_user()["id"]
    r = db().execute("SELECT * FROM notifications WHERE id=? AND user_id=?", (nid, uid)).fetchone()
    if not r:
        abort(404)
    db().execute("UPDATE notifications SET is_read=1 WHERE id=?", (nid,))
    db().commit()
    dest = r["url"] or "/"
    return redirect(dest if dest.startswith("/") and not dest.startswith("//") else "/")


@bp.post("/notifications/clear")
@login_required
def notifications_clear():
    db().execute("DELETE FROM notifications WHERE user_id=?", (current_user()["id"],))
    db().commit()
    return redirect(url_for("web.notifications"))


# ------------------------------------------------------------------ QR code for the app (logo in the middle)
_qr_cache = {}


def _app_url():
    return (os.environ.get("SITE_URL") or request.host_url).rstrip("/") + "/"


def _logo_image():
    from PIL import Image
    fid = setting("web_logo")
    path = fetch_file(fid) if fid else None
    try:
        return Image.open(path) if path else Image.open(io.BytesIO(_default_logo_png(256)))
    except Exception:
        return None


def _font(size, bold=True):
    from PIL import ImageFont
    names = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf"] if bold
             else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _poster(url, logo):
    from PIL import Image, ImageDraw
    W, H = 1080, 1560
    gold, soft = (214, 168, 64), (235, 215, 160)
    im = Image.new("RGB", (W, H), (14, 14, 14))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([28, 28, W - 29, H - 29], radius=36, outline=gold, width=4)

    def centred(text, y, font, fill):
        w = d.textlength(text, font=font)
        d.text(((W - w) / 2, y), text, font=font, fill=fill)
    if logo is not None:
        side = min(logo.size)
        w, h = logo.size
        em = logo.convert("RGB").crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize((230, 230))
        mask = Image.new("L", (920, 920), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 919, 919], fill=255)
        im.paste(em, ((W - 230) // 2, 90), mask.resize((230, 230), Image.LANCZOS))
    centred("AD FASHION SHOP", 350, _font(78), gold)
    centred("Scan to open our app", 450, _font(46, False), (255, 255, 255))
    qr = qr_image(url, logo=logo, scale=14, border=2).resize((760, 760), Image.LANCZOS)
    d.rounded_rectangle([140, 540, W - 141, 540 + 800], radius=40, fill="white")
    im.paste(qr, ((W - 760) // 2, 560))
    centred("Hijabs  |  Jilbabs  |  Textiles & more", 1370, _font(36), soft)
    centred("Android: open the link in Chrome > menu > Install app", 1428, _font(27, False), (190, 190, 190))
    centred("iPhone: open in Safari > Share > Add to Home Screen", 1468, _font(27, False), (190, 190, 190))
    return im


def _png(im, name, dl):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    r = Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "public, max-age=300"})
    if dl:
        r.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return r


@bp.get("/qr.png")
def qr_png():
    url = _app_url()
    key = ("qr", url, setting("web_logo"))
    if key not in _qr_cache:
        _qr_cache.clear()
        _qr_cache[key] = qr_image(url, logo=_logo_image(), scale=16, border=4)
    return _png(_qr_cache[key], "AD-FASHION-SHOP-QR.png", request.args.get("dl"))


@bp.get("/qr-card.png")
def qr_card():
    url = _app_url()
    key = ("card", url, setting("web_logo"))
    if key not in _qr_cache:
        _qr_cache.clear()
        _qr_cache[key] = _poster(url, _logo_image())
    return _png(_qr_cache[key], "AD-FASHION-SHOP-poster.png", request.args.get("dl"))


@bp.get("/qr")
def qr_page():
    return page("Share the app", """
<h2>Share the app</h2><p class="mut small" style="margin-top:0">Anyone who scans this code with their phone camera opens AD FASHION SHOP straight away.</p>
<div class="card" style="padding:14px;text-align:center"><img src="{{ url_for('web.qr_card') }}?v={{ v }}" alt="QR code poster" style="width:100%;max-width:360px;border-radius:12px">
<div class="row" style="justify-content:center;margin-top:12px">
<a class="btn gold sm" href="{{ url_for('web.qr_card', dl=1) }}">⬇ Download poster</a>
<a class="btn alt sm" href="{{ url_for('web.qr_png', dl=1) }}">⬇ QR code only</a></div>
<p class="mut small" style="margin:.8em 0 0;word-break:break-all">{{ link }}</p></div>
<p class="mut small">Print the poster and put it in your shop, or share the picture on WhatsApp status and Instagram.</p>""",
                tab="user", link=_app_url(), v=hashlib.sha256((setting("web_logo") or "d").encode()).hexdigest()[:6])


@bp.get("/push/key")
def push_key():
    c = sqlite3.connect(CFG["DB"])
    try:
        return {"key": vapid_keys(c)[1]}
    finally:
        c.close()


@bp.post("/push/subscribe")
def push_subscribe():
    d = request.get_json(silent=True) or {}
    k = d.get("keys") or {}
    ep, p256, auth = d.get("endpoint", ""), k.get("p256dh", ""), k.get("auth", "")
    if not (ep.startswith("https://") and p256 and auth):
        abort(400)
    db().execute("INSERT INTO push_subs(user_id,endpoint,p256dh,auth) VALUES(?,?,?,?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,p256dh=excluded.p256dh,auth=excluded.auth",
                 (current_user()["id"], ep, p256, auth))
    db().commit()
    return {"ok": True}


@bp.post("/push/unsubscribe")
def push_unsubscribe():
    d = request.get_json(silent=True) or {}
    db().execute("DELETE FROM push_subs WHERE endpoint=?", (d.get("endpoint", ""),))
    db().commit()
    return {"ok": True}


@bp.get("/sw.js")
def service_worker():
    js = """
const CACHE='ad-shop-v3';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim());});
self.addEventListener('push',e=>{
  let d={};try{d=e.data.json();}catch(_){d={title:'AD FASHION SHOP',body:e.data?e.data.text():''};}
  e.waitUntil(self.registration.showNotification(d.title||'AD FASHION SHOP',{body:d.body||'',icon:'/icon-192.png',badge:'/icon-192.png',tag:d.tag||undefined,renotify:!!d.tag,data:{url:d.url||'/'}}));
});
self.addEventListener('notificationclick',e=>{
  e.notification.close();const u=(e.notification.data&&e.notification.data.url)||'/';
  e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(l=>{for(const c of l){if('focus' in c){c.navigate(u);return c.focus();}}return clients.openWindow(u);}));
});
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
{% if register %}<label class="l">Email</label><input type="email" name="email" required autocomplete="email" autocapitalize="off">
<label class="l">Phone number</label><input type="tel" name="phone" required inputmode="tel" autocomplete="tel" placeholder="e.g. 08012345678">
{% else %}<label class="l">Email or phone number</label><input type="text" name="email" required autocomplete="username" autocapitalize="off">{% endif %}
<label class="l">Password</label><input type="password" name="password" required minlength="6" autocomplete="{{ 'new-password' if register else 'current-password' }}">
<p><button class="btn gold block">{{ button }}</button></p></form>
{% if gid %}<div style="display:flex;align-items:center;gap:10px;margin:14px 0"><hr style="flex:1;border:0;border-top:1px solid #ddd"><span class="mut small">or</span><hr style="flex:1;border:0;border-top:1px solid #ddd"></div>
<div id="gbtn" style="display:flex;justify-content:center;min-height:44px"></div><div id="gerr" class="small" style="color:#b00020;text-align:center"></div>
<script src="https://accounts.google.com/gsi/client" async defer></script>
<script>
window.addEventListener('load',function(){var t=setInterval(function(){if(!window.google||!google.accounts)return;clearInterval(t);
google.accounts.id.initialize({client_id:'{{ gid }}',callback:function(r){
 fetch('/auth/google',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF':'{{ csrf }}'},body:JSON.stringify({credential:r.credential})})
 .then(function(x){return x.json()}).then(function(d){if(d.ok){location.href=d.next||'/';}else{document.getElementById('gerr').textContent=d.error||'Google sign-in failed.';}})
 .catch(function(){document.getElementById('gerr').textContent='Google sign-in failed. Check your connection.';});}});
google.accounts.id.renderButton(document.getElementById('gbtn'),{theme:'outline',size:'large',text:'continue_with',shape:'pill',width:300});},200);});
</script>{% endif %}
<p style="text-align:center">{{ switch|safe }}</p></div>"""


@bp.route("/", methods=["GET"])
def home():
    u = current_user()
    if not u:
        return page("Welcome", WELCOME)
    d = db()
    new = new_arrivals(10)
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
{% if new %}<div class="sec"><h3>New arrivals</h3><a href="{{ url_for('web.new_arrivals_page') }}">See all</a></div>
<div class="hscroll">{% for p in new %}{{ pcard(p) }}{% endfor %}</div>{% endif %}
{% if latest %}<div class="sec"><h3>Latest news</h3><a href="{{ url_for('web.news') }}">All news</a></div>
{% for n in latest %}<a class="card" href="{{ url_for('web.news_post', nid=n['id']) }}#p" style="display:block;padding:14px;margin-bottom:10px;text-decoration:none">
<span class="tag {{ 'g' if n['kind']=='giveaway' }}">{{ n['kind']|title }}</span><h3>{{ n['title'] }}</h3>
<p class="mut small" style="margin:.2em 0">{{ n['body'][:110] }}{% if n['body']|length>110 %}…{% endif %}</p></a>{% endfor %}{% endif %}
<p><a class="btn wa block" href="https://wa.me/{{ cfg.WHATSAPP }}">{{ icon('chat') }} Chat with us on WhatsApp</a></p>
""", tab="home", pct=discount_pct(), tiles=tiles, new=new, latest=latest, flyer=bool(setting("web_flyer")))


def _announce_signup(name, email, phone=""):
    total = db().execute("SELECT COUNT(*) FROM web_users").fetchone()[0]
    tg_notify(f"🆕 New app sign-up\n👤 {name}\n📧 {email}\n📞 {phone or '(phone pending)'}\nTotal app customers: {total}")
    push_admins("🆕 New sign-up", f"{name} ({phone or email}) just joined. Total customers: {total}", "/admin/customers", tag="signup")


def _verify_google(credential):
    """Ask Google to check the sign-in token. Returns its claims, or None if invalid for this app."""
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not cid or not credential:
        return None
    try:
        raw = urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(credential), timeout=10).read()
        d = json.loads(raw)
    except Exception:
        return None
    ok = (d.get("aud") == cid and d.get("iss") in ("accounts.google.com", "https://accounts.google.com")
          and str(d.get("email_verified")).lower() == "true" and int(d.get("exp", 0)) > time.time() and d.get("email"))
    return d if ok else None


@bp.post("/auth/google")
def google_auth():
    d = request.get_json(silent=True) or {}
    info = _verify_google(d.get("credential", ""))
    if not info:
        return {"ok": False, "error": "Google sign-in could not be verified. Please try again."}, 400
    email = info["email"].strip().lower()
    u = db().execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()
    if u:
        uid = u["id"]
    else:
        name = (info.get("name") or email.split("@")[0])[:80]
        cur = db().execute("INSERT INTO web_users(email,name,phone,password_hash) VALUES(?,?,?,?)",
                           (email, name, "", generate_password_hash(secrets.token_urlsafe(24))))
        db().commit()
        uid = cur.lastrowid
        _announce_signup(name, email)
    start_session(uid, session.get("cart"))
    return {"ok": True, "next": url_for("web.home")}


@bp.route("/register", methods=["GET", "POST"])
def register_page():
    if current_user():
        return redirect(url_for("web.home"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80]
        email = request.form.get("email", "").strip().lower()[:120]
        pw = request.form.get("password", "")
        phone = norm_phone(request.form.get("phone", ""))
        if not name or not EMAIL_RE.match(email) or len(pw) < 6:
            flash("Enter your name, a valid email and a password of 6+ characters.")
        elif not phone:
            flash("Please enter a valid phone number.")
        elif db().execute("SELECT 1 FROM web_users WHERE email=?", (email,)).fetchone():
            flash("That email already has an account. Please log in.")
        elif db().execute("SELECT 1 FROM web_users WHERE phone=?", (phone,)).fetchone():
            flash("That phone number already has an account. Please log in.")
        else:
            cur = db().execute("INSERT INTO web_users(email,name,phone,password_hash) VALUES(?,?,?,?)",
                               (email, name, phone, generate_password_hash(pw)))
            db().commit()
            start_session(cur.lastrowid)
            _announce_signup(name, email, phone)
            flash("Welcome to AD FASHION SHOP! You've earned 10 style points.")
            return redirect(url_for("web.home"))
    return page("Create account", AUTH_FORM, register=True, gid=os.environ.get("GOOGLE_CLIENT_ID", "").strip(), heading="Create your account",
                sub="Create your account with your email, phone number and a password.", button="Create account",
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
            if "@" in email:
                u = db().execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()
            else:
                ph = norm_phone(email)
                u = db().execute("SELECT * FROM web_users WHERE phone=?", (ph,)).fetchone() if ph else None
            if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
                start_session(u["id"], session.get("cart"))
                return redirect(safe_next(url_for("web.home")))
            _login_attempts[ip] = hits + [time.time()]
            flash("Wrong email/phone or password.")
    return page("Log in", AUTH_FORM, register=False, gid=os.environ.get("GOOGLE_CLIENT_ID", "").strip(), heading="Welcome back", sub="Log in to continue shopping.",
                button="Log in", switch=Markup('New here? <a href="%s"><b>Create an account</b></a>' % url_for("web.register_page")))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.home"))


# ================================================================== shop
@bp.get("/new")
def new_arrivals_page():
    items = new_arrivals()
    return page("New arrivals", """
<h2>New arrivals</h2><p class="mut small" style="margin-top:0">Everything added in the last 30 days, newest first.</p>
{% if not items %}<div class="card" style="padding:28px;text-align:center"><div style="font-size:34px">✨</div><p class="mut">No new products right now. Check the full shop.</p><a class="btn gold" href="{{ url_for('web.shop') }}">Browse the shop</a></div>{% endif %}
<div class="grid">{% for p in items %}{{ pcard(p) }}{% endfor %}</div>
{% if items %}<p style="text-align:center;margin-top:14px"><a class="btn sm" href="{{ url_for('web.shop') }}">Browse the full shop</a></p>{% endif %}""", tab="shop", items=items)


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
    fp = fabric_prices(pid) if is_color_cat else {k: v for k, v in fabric_prices(pid).items() if is_unit_key(k)}
    by_unit = bool(fp) and not is_color_cat
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
{% if fp %}<label class="l">{{ 'How do you want to buy?' if by_unit else 'Choose fabric' }}</label>
{% for k,v in fp.items() %}<label class="opt"><input type="radio" name="fabric" value="{{ k }}" required {{ 'checked' if loop.first }}><span class="grow"><b>{{ fabric_name[k] }}</b></span><span class="price">{{ money(v) }}</span></label>{% endfor %}{% endif %}
{% if colors %}<label class="l">Choose colour</label><div class="swatches">
{% for co in colors %}<label class="sw"><input type="radio" name="color" value="{{ co['id'] }}" {{ 'checked' if loop.first }}><img src="{{ url_for('web.img', fid=co['photo_file_id']) }}" alt="Colour {{ co['id'] }}"></label>{% endfor %}</div>{% endif %}
<label class="l" id="qlab">{{ 'How many?' if by_unit else 'Quantity' }}</label>
<div class="qty"><button type="button" onclick="st(-1)">−</button><input type="number" name="qty" id="qty" value="1" min="{{ 0.25 if by_unit else 1 }}" max="500" step="{{ 0.25 if by_unit else 1 }}" inputmode="{{ 'decimal' if by_unit else 'numeric' }}" oninput="upd()"><button type="button" onclick="st(1)">+</button></div>
{% if by_unit %}<p class="mut small" style="margin:.4em 0 0">You can use decimals, for example 2.5.</p>{% endif %}
<div class="row" style="margin-top:18px"><button class="btn gold grow">{{ icon('cart') }} Add to cart</button>
<a class="btn wa" href="{{ share }}" target="_blank" rel="noopener">Share</a></div></form>
<form method="post" action="{{ url_for('web.wish', pid=p['id']) }}" style="margin-top:10px"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="btn alt sm">{{ icon('heart') }} {{ 'Saved to wishlist' if wished else 'Save for later' }}</button></form>
</div></div>
<script>
var FP={{ fp|tojson }},BASE={{ p['price']|tojson }},UNIT={{ by_unit|tojson }},UW={{ unit_word|tojson }};
function upd(){var f=document.querySelector('input[name=fabric]:checked');var u=f?FP[f.value]:BASE;var q=parseFloat(document.getElementById('qty').value);if(!(q>0))q=UNIT?0.25:1;if(!UNIT)q=Math.max(1,Math.floor(q));
var el=document.getElementById('price');if(u==null){return}var w=(f&&UNIT)?UW[f.value]:'';
var t=Math.round(u*q);el.textContent='₦'+t.toLocaleString()+(UNIT?'  ('+q+' '+w+(q==1?'':'s')+' × ₦'+u.toLocaleString()+')':(q>1?'  (₦'+u.toLocaleString()+' each)':''));
var ql=document.getElementById('qlab');if(ql&&UNIT&&w)ql.textContent='How many '+w+'s?';}
function st(n){var i=document.getElementById('qty');if(UNIT){i.value=Math.min(500,Math.max(0.5,Math.round(((parseFloat(i.value)||1)+n*0.5)*4)/4));}else{i.value=Math.min(500,Math.max(1,(parseInt(i.value)||1)+n));}upd();}
document.querySelectorAll('input[name=fabric]').forEach(function(r){r.addEventListener('change',upd)});if(Object.keys(FP).length)upd();
var g=document.getElementById('gal'),ds=document.querySelectorAll('#dots i');
if(g&&ds.length){g.addEventListener('scroll',function(){var i=Math.round(g.scrollLeft/g.clientWidth);ds.forEach(function(d,k){d.className=k==i?'on':''})});}
</script>""", tab="shop", p=p, photos=photos, colors=colors, fp=fp, wished=wished, share=share, by_unit=by_unit)


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
    is_color_cat = (p["category"] or "").lower() in color_categories()
    unit_fp = {} if is_color_cat else {k: v for k, v in fabric_prices(pid).items() if is_unit_key(k)}
    fab, cid = None, None
    if unit_fp:
        fab = request.form.get("fabric")
        qty = parse_qty(request.form.get("qty"), decimal=True, lo=0.25)
        if fab not in unit_fp:
            flash("Please choose Per yard or Per trouser first.")
            return redirect(url_for("web.product", pid=pid))
        if qty is None:
            flash("Please enter how many (for example 2 or 2.5).")
            return redirect(url_for("web.product", pid=pid))
    else:
        qty = to_int(request.form.get("qty"), 1, 1, 500)
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
    if key in cart():
        if is_unit_key(parse_key(key)[2]):
            try:
                raw = float(str(request.form.get("qty", "1")).replace(",", "."))
            except ValueError:
                raw = 1
            qty = 0 if raw <= 0 else (parse_qty(raw, decimal=True, lo=0.25) or 0.25)
        else:
            qty = to_int(request.form.get("qty"), 1)
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
{% if i.unit %}<div class="small mut">{{ fq(i.qty) }} {{ i.unit }}{{ '' if i.qty==1 else 's' }} × {{ money(i.price) }}</div>{% endif %}
<form method="post" action="{{ url_for('web.cart_update') }}" class="row" style="margin-top:6px;gap:6px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="key" value="{{ i.key }}">
<div class="qty"><button name="qty" value="{{ fq(i.qty-i.step) }}" style="width:36px;height:36px">−</button><input value="{{ fq(i.qty) }}" readonly style="width:48px"><button name="qty" value="{{ fq(i.qty+i.step) }}" style="width:36px;height:36px">+</button></div>
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
            lines.append(f"{i['p']['name']}{extra} x{fmt_qty(i['qty'])}{(' ' + i['unit'] + 's') if i['unit'] and i['qty'] != 1 else (' ' + i['unit'] if i['unit'] else '')} - {money(i['line'])}")
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
        if not norm_phone(u["phone"]) and norm_phone(phone) and not d.execute("SELECT 1 FROM web_users WHERE phone=?", (norm_phone(phone),)).fetchone():
            d.execute("UPDATE web_users SET phone=? WHERE id=?", (norm_phone(phone), u["id"]))
        d.commit()
        session["cart"] = {}
        tg_notify(f"🌐 New APP order #AD-{oid:05d}\n{u['name']} ({u['email']})\n{phone}\n{DELIVERY[method]}\n"
                  f"{addr} {city} {state}\n\n" + "\n".join(lines) + f"\n\nTotal: {money(grand)}")
        push_admins(f"🛍️ New order #AD-{oid:05d}", f"{u['name']} ordered {money(grand)}", f"/admin/order/{oid}", tag="order")
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
<p class="mut small" style="margin:.2em 0 0">⭐ You will earn about {{ pts }} style points when this order is delivered.</p>
<p><button class="btn gold block">Place order</button></p></form>""",
                tab="shop", total=total, items=items, dm=DELIVERY, off=off, pct=discount_pct(), pts=points_for(total - off),
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
    push_admins("✅ RECEIVED AND THANK YOU", f"{o['customer_name']} confirmed order #AD-{oid:05d} was received.", f"/admin/order/{oid}", tag="received")
    tg_notify(f"✅ RECEIVED AND THANK YOU\n{o['customer_name']} confirmed that order #AD-{oid:05d} ({money(o['total'])}) was received.")
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
            push_admins("💳 Payment received", f"Order #AD-{oid:05d} paid online ({money(o['total'])}).", f"/admin/order/{oid}", tag="pay")
            push_order_status(oid, "PAID")
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
def _user_gallery(uid, limit=18):
    """Products this customer bought or saved, newest first (for the profile photo gallery)."""
    rows = db().execute("""SELECT p.id, p.name, p.photo_file_id FROM products p WHERE p.id IN (
            SELECT product_id FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE web_user_id=?)
            UNION SELECT product_id FROM web_wishlist WHERE user_id=?) AND p.photo_file_id<>'' LIMIT ?""",
                       (uid, uid, limit)).fetchall()
    return rows


def _user_stats(uid):
    r = db().execute("""SELECT COUNT(*) n, COALESCE(SUM(total),0) spent,
                        COALESCE(SUM(CASE WHEN status='DELIVERED' THEN 1 ELSE 0 END),0) done
                        FROM orders WHERE web_user_id=? AND status<>'CANCELLED'""", (uid,)).fetchone()
    w = db().execute("SELECT COUNT(*) FROM web_wishlist WHERE user_id=?", (uid,)).fetchone()[0]
    return {"orders": r["n"], "spent": r["spent"], "delivered": r["done"], "wish": w}


@bp.get("/profile")
def profile():
    u = current_user()
    st = _user_stats(u["id"])
    return page("Profile", """
<div class="card" style="padding:18px"><div class="row" style="gap:16px"><div class="avatar">{{ user['name'][:1]|upper }}</div>
<div class="grow"><h2>{{ user['name'] }}</h2><div class="mut small">{{ user['email'] }}</div><div class="mut small">{{ user['phone'] }} · member since {{ user['created_at'][:10] }}</div></div></div>
<div class="kpi" style="margin-top:14px"><div><span class="mut small">Style points</span><b>{{ user['score'] }}</b></div><div><span class="mut small">Orders</span><b>{{ st.orders }}</b></div><div><span class="mut small">Delivered</span><b>{{ st.delivered }}</b></div></div>
<div class="row between" style="margin-top:10px"><span class="mut small">Total spent</span><b>{{ money(st.spent) }}</b></div>
<p class="mut small" style="margin:.8em 0 0">⭐ 10 points for joining, plus 1 point for every ₦1,000 you spend (added when your order is delivered). Bigger orders earn more.</p></div>
{% if gallery %}<h3 style="margin-top:18px">My gallery</h3>
<div style="display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:6px">{% for g in gallery %}
<a href="{{ url_for('web.product', pid=g['id']) }}" style="flex:none;width:96px;scroll-snap-align:start;text-decoration:none;color:inherit"><img src="{{ url_for('web.img', fid=g['photo_file_id']) }}" alt="" style="width:96px;height:96px;object-fit:cover;border-radius:12px;display:block"><div class="small" style="margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ g['name'] }}</div></a>{% endfor %}</div>
<p class="mut small" style="margin:2px 0 0">Items you ordered or saved.</p>{% endif %}
<div class="card menu" style="margin-top:14px">
<a href="{{ url_for('web.orders') }}">{{ icon('orders') }} My orders ({{ st.orders }})</a>
<a href="{{ url_for('web.wishlist') }}">{{ icon('heart') }} Wishlist ({{ st.wish }})</a>
<a href="{{ url_for('web.notifications') }}">{{ icon('bell') }} Notifications</a>
<a href="{{ url_for('web.qr_page') }}">{{ icon('share') }} Share the app (QR code)</a>
<a href="https://wa.me/{{ cfg.WHATSAPP }}">{{ icon('chat') }} Contact us on WhatsApp</a>
{% if is_admin %}<a href="{{ url_for('web.admin_home') }}">{{ icon('admin') }} Admin panel</a>{% endif %}</div>
<h3 style="margin-top:20px">Edit profile</h3>
<form method="post" action="{{ url_for('web.profile_update') }}" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Full name</label><input name="name" value="{{ user['name'] }}" required>
<label class="l">Phone</label><input name="phone" value="{{ user['phone'] }}" inputmode="tel" required>
<p><button class="btn block">Save</button></p></form>
<h3 style="margin-top:20px">Change password</h3>
<form method="post" action="{{ url_for('web.password_change') }}" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Current password</label><input type="password" name="old" required autocomplete="current-password">
<label class="l">New password</label><input type="password" name="new" minlength="6" required autocomplete="new-password">
<p><button class="btn block">Change password</button></p></form>
<form method="post" action="{{ url_for('web.logout') }}" style="margin-top:20px"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="btn bad block">Sign out</button></form>
<p class="mut small" style="text-align:center">You stay signed in on this phone until you sign out.</p>""", tab="user", st=st, gallery=_user_gallery(u["id"]))


@bp.route("/complete-profile", methods=["GET", "POST"])
def complete_profile():
    u = current_user()
    if not u:
        return redirect(url_for("web.home"))
    if norm_phone(u["phone"]):
        return redirect(url_for("web.home"))
    if request.method == "POST":
        ph = norm_phone(request.form.get("phone", ""))
        if not ph:
            flash("Please enter a valid phone number.")
        elif db().execute("SELECT 1 FROM web_users WHERE phone=? AND id<>?", (ph, u["id"])).fetchone():
            flash("That phone number is already used by another account.")
        else:
            db().execute("UPDATE web_users SET phone=? WHERE id=?", (ph, u["id"]))
            db().commit()
            return redirect(safe_next(url_for("web.home")))
    return page("Add phone number", """<div style="max-width:420px;margin:0 auto;padding:10px 0">
<div style="text-align:center;margin:10px 0 18px"><div style="font-size:40px">📞</div><h2>One last step</h2><p class="mut small">Please add your phone number. We use it to reach you about your orders and deliveries.</p></div>
<form method="post" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Phone number</label><input type="tel" name="phone" required inputmode="tel" autocomplete="tel" placeholder="e.g. 08012345678" autofocus>
<p><button class="btn gold block">Continue</button></p></form>
<form method="post" action="{{ url_for('web.logout') }}" style="text-align:center"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn sm">Sign out</button></form></div>""")


@bp.post("/profile")
def profile_update():
    name = request.form.get("name", "").strip()[:80]
    phone = norm_phone(request.form.get("phone", ""))
    uid = current_user()["id"]
    if not name or not phone:
        flash("Please enter your name and a valid phone number.")
    elif db().execute("SELECT 1 FROM web_users WHERE phone=? AND id<>?", (phone, uid)).fetchone():
        flash("That phone number is already used by another account.")
    else:
        db().execute("UPDATE web_users SET name=?, phone=? WHERE id=?", (name, phone, uid))
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
{% for ep,label in [('web.admin_home','Dashboard'),('web.admin_orders','Orders'),('web.admin_products','Products'),('web.admin_categories','Categories'),('web.admin_colors','Colours'),('web.admin_news','News'),('web.admin_settings','Settings'),('web.admin_customers','Customers'),('web.admin_share','📣 Share'),('web.qr_page','📲 QR'),('web.admin_thanks','💛 Thank you'),('web.admin_pin_change','🔐 PIN')] %}
<a class="pill {{ 'on' if request.endpoint==ep }}" href="{{ url_for(ep) }}">{{ label }}</a>{% endfor %}</div>"""


def admin_page(title, body, **ctx):
    return page(title, ADMIN_NAV + body, tab="", **ctx)


def reload_cats():
    fn = CFG.get("reload_categories")
    if fn:
        fn()


PIN_FORM = """<div style="max-width:380px;margin:0 auto;padding:10px 0">
<div style="text-align:center;margin:10px 0 18px"><div style="font-size:40px">🔐</div><h2>{{ heading }}</h2><p class="mut small">{{ sub }}</p></div>
<form method="post" class="card" style="padding:16px"><input type="hidden" name="csrf" value="{{ csrf }}">
{% if mode=='change' %}<label class="l">Current PIN</label><input type="password" name="old" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="off">{% endif %}
<label class="l">{{ 'New 6-digit PIN' if mode in ('create','change') else '6-digit PIN' }}</label>
<input type="password" name="pin" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" minlength="6" required autocomplete="off" autofocus style="letter-spacing:10px;text-align:center;font-size:22px">
{% if mode in ('create','change') %}<label class="l">Repeat PIN</label><input type="password" name="pin2" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="off" style="letter-spacing:10px;text-align:center;font-size:22px">{% endif %}
<p><button class="btn gold block">{{ button }}</button></p></form>
<p class="mut small" style="text-align:center">Forgot your PIN? Reset it from the Telegram bot: Admin, Settings, 🔐 Reset App PIN.</p></div>"""


def _pin_ok_format(p):
    return len(p) == 6 and p.isdigit() and len(set(p)) > 1 and p not in ("123456", "654321")


def _pin_locked_msg():
    until = float(setting("admin_pin_lock_until", "0") or 0)
    if time.time() < until:
        return "Too many wrong PINs. Try again in %d minutes." % (int((until - time.time()) // 60) + 1)
    return ""


def _pin_fail():
    n = int(setting("admin_pin_fails", "0") or 0) + 1
    if n >= 5:
        set_setting("admin_pin_lock_until", time.time() + 600)
        n = 0
    set_setting("admin_pin_fails", n)


@bp.route("/admin/pin", methods=["GET", "POST"])
def admin_pin():
    if not current_user():
        return redirect(url_for("web.login"))
    if not is_admin_user():
        abort(403)
    nxt = safe_next(url_for("web.admin_home"))
    em = _admin_email()
    creating = not admin_pin_set()
    if request.method == "POST":
        pin = request.form.get("pin", "").strip()
        if creating:
            if not _pin_ok_format(pin):
                flash("Use 6 digits that are not all the same or a simple run like 123456.")
            elif pin != request.form.get("pin2", "").strip():
                flash("The two PINs do not match.")
            else:
                set_setting("admin_pin:" + em, generate_password_hash(pin))
                session["admin_ok"], session["admin_for"], session["admin_seen"] = True, em, time.time()
                flash("Admin PIN created. Keep it private.")
                return redirect(nxt)
        else:
            msg = _pin_locked_msg()
            if msg:
                flash(msg)
            elif check_password_hash(setting("admin_pin:" + em), pin):
                set_setting("admin_pin_fails", 0)
                session["admin_ok"], session["admin_for"], session["admin_seen"] = True, em, time.time()
                return redirect(nxt)
            else:
                _pin_fail()
                flash("Wrong PIN.")
    return page("Admin PIN", PIN_FORM, mode="create" if creating else "enter",
                heading="Create your admin PIN" if creating else "Admin PIN",
                sub="Choose a 6-digit PIN. You will be asked for it every time you open the app." if creating
                else "Enter your 6-digit PIN to open the admin area.",
                button="Save PIN" if creating else "Unlock")


@bp.route("/admin/pin/change", methods=["GET", "POST"])
@admin_required
def admin_pin_change():
    em = _admin_email()
    if request.method == "POST":
        if not check_password_hash(setting("admin_pin:" + em), request.form.get("old", "").strip()):
            flash("Current PIN is wrong.")
        elif not _pin_ok_format(request.form.get("pin", "").strip()):
            flash("Use 6 digits that are not all the same or a simple run like 123456.")
        elif request.form.get("pin", "").strip() != request.form.get("pin2", "").strip():
            flash("The two PINs do not match.")
        else:
            set_setting("admin_pin:" + em, generate_password_hash(request.form["pin"].strip()))
            flash("PIN changed.")
            return redirect(url_for("web.admin_settings"))
    return page("Change PIN", PIN_FORM, mode="change", heading="Change admin PIN", sub="Enter your current PIN, then a new one.", button="Change PIN")


@bp.post("/admin/lock")
def admin_lock_route():
    admin_lock()
    return ("", 204)


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
    push_order_status(oid, st)
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
        for fk, _ in (FABRICS if category in color_categories() else UNITS):
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
<div id="units" style="display:none"><label class="l">Textiles: price per yard / per trouser (₦) — optional. Customers choose one and type how many (2.5 works). Example: ₦1,000 per yard, ₦1,500 per trouser, so 2.5 yards = ₦2,500.</label>
{% for k,lab in units %}<div class="row" style="flex-wrap:nowrap;margin-bottom:6px"><span class="grow">{{ lab }}</span><input name="fab_{{ k }}" value="{{ fp.get(k,'') }}" inputmode="numeric" style="max-width:150px" placeholder="not set"></div>{% endfor %}</div>
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
function sync(){var c=ce.value;document.getElementById('fabs').style.display=cs.indexOf(c)>=0?'block':'none';document.getElementById('units').style.display=cs.indexOf(c)>=0?'none':'block';
for(var i=0;i<se.options.length;i++){var o=se.options[i];if(!o.dataset.cat)continue;var ok=o.dataset.cat==c;o.hidden=!ok;o.disabled=!ok;if(!ok&&o.selected)se.value='';}}
ce.addEventListener('change',sync);sync();
</script>""", p=p, is_new=is_new, cats=cats(), subs=CFG.get("SUBCATEGORIES", {}), clean=clean_label, photos=photos,
                      fp=fp, fabrics=FABRICS, units=UNITS, color_cats=sorted(color_categories()))


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
        push_all_customers("📰 " + title[:60], body[:120], "/news")
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
THANKS_DEFAULT = ("Assalamu alaikum {name}, thank you for joining the AD FASHION SHOP app! 💛 "
                  "We appreciate you. Browse our hijabs, jilbabs, textiles and more anytime. - AD FASHION HIJABS & MORE, Bauchi")


def _personalise(text, full_name):
    """Put the customer's first name into the message. If the text has no {name}, a greeting with the name is added."""
    first = (full_name or "").strip().split(" ")[0] or "dear customer"
    return text.replace("{name}", first) if "{name}" in text else f"Assalamu alaikum {first}, {text}"


@bp.route("/admin/thanks", methods=["GET", "POST"])
@admin_required
def admin_thanks():
    if request.method == "POST":
        act = request.form.get("act")
        text = request.form.get("text", "").strip()[:700] or THANKS_DEFAULT
        set_setting("thanks_text", text)
        if act == "notify":                      # in-app + phone notification to every customer at once
            rows = db().execute("SELECT id,name FROM web_users").fetchall()
            for r in rows:
                push_users([r["id"]], "💛 Thank you", _personalise(text, r["name"]), "/", tag="thanks")
            flash(f"Thank-you notification sent to {len(rows)} customers.")
        else:
            flash("Message saved.")
        return redirect(url_for("web.admin_thanks"))
    rows = db().execute("""SELECT u.id,u.name,u.phone,u.created_at,w.sent_at FROM web_users u
                           LEFT JOIN wa_sent w ON w.user_id=u.id ORDER BY (w.sent_at IS NOT NULL), u.id DESC""").fetchall()
    return admin_page("Thank you", """<h2>Thank-you messages</h2>
<form method="post" class="card" style="padding:14px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label class="l">Your message. The customer's name is added automatically (write {name} where you want it; if you leave it out, the message starts with "Assalamu alaikum NAME,")</label>
<textarea id="tt" name="text" rows="5">{{ text }}</textarea>
<div class="row" style="margin-top:8px"><button class="btn alt sm" name="act" value="save">Save message</button>
<button class="btn gold sm" name="act" value="notify" onclick="return confirm('Send this as an app notification to all customers?')">🔔 Send to everyone in the app</button></div></form>
<h3 style="margin-top:16px">Send on WhatsApp ({{ rows|selectattr('sent_at')|list|length }} of {{ rows|length }} sent)</h3>
<p class="mut small">Tap a name's button. WhatsApp opens with the message ready for that person. Just press send.</p>
{% for u in rows %}<div class="card" id="u{{ u['id'] }}" style="padding:12px;margin-bottom:8px;{{ 'opacity:.55;' if u['sent_at'] }}"><div class="row between" style="flex-wrap:nowrap">
<div><b>{{ u['name'] }}</b><div class="small mut">{{ u['phone'] or 'no phone number' }}</div></div>
{% if u['phone'] %}<a class="btn wa sm" data-wa="1" data-uid="{{ u['id'] }}" data-name="{{ u['name'].split(' ')[0] }}" data-ph="{{ u['phone']|replace('+','') }}" href="#" target="_blank" rel="noopener"><span class="st">{{ '✓ Sent · again' if u['sent_at'] else 'WhatsApp' }}</span></a>{% endif %}</div></div>{% endfor %}
<script>
document.querySelectorAll('[data-wa]').forEach(function(a){a.addEventListener('click',function(){
 var raw=document.getElementById('tt').value;var m=raw.indexOf('{name}')>-1?raw.replace(/\\{name\\}/g,a.dataset.name):('Assalamu alaikum '+a.dataset.name+', '+raw);
 a.href='https://wa.me/'+a.dataset.ph+'?text='+encodeURIComponent(m);
 fetch('/admin/thanks/'+a.dataset.uid+'/mark',{method:'POST',credentials:'same-origin',headers:{'X-CSRF':'{{ csrf }}'}});
 a.querySelector('.st').textContent='✓ Sent · again';document.getElementById('u'+a.dataset.uid).style.opacity='.55';});});
</script>""", rows=rows, text=setting("thanks_text", THANKS_DEFAULT))


@bp.post("/admin/thanks/<int:uid>/mark")
@admin_required
def admin_thanks_mark(uid):
    db().execute("INSERT INTO wa_sent(user_id) VALUES(?) ON CONFLICT(user_id) DO UPDATE SET sent_at=CURRENT_TIMESTAMP", (uid,))
    db().commit()
    return ("", 204)


# ================================================================== SOCIAL SHARING (Facebook Page + Instagram)
GRAPH = "https://graph.facebook.com/v21.0"


IG_GRAPH = "https://graph.instagram.com/v21.0"


def _graph(method, path, params, base=None):
    """Call Meta's Graph API. Returns the JSON answer or raises RuntimeError with Meta's own message."""
    data = urllib.parse.urlencode(params)
    req = urllib.request.Request((base or GRAPH) + path + ("?" + data if method == "GET" else ""),
                                 data=None if method == "GET" else data.encode(), method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=40).read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("error", {}).get("message")
        except Exception:
            msg = None
        raise RuntimeError(msg or f"Meta answered HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(str(e) or "Could not reach Facebook")


def social_ready():
    return {"fb": bool(setting("meta_page_token") and setting("meta_fb_page_id")),
            "ig": bool((setting("meta_ig_token") or setting("meta_page_token")) and setting("meta_ig_id"))}


def _ig_target():
    """(base_url, token) for Instagram: the Instagram-only login if connected, else the Facebook-page login."""
    if setting("meta_ig_token"):
        return IG_GRAPH, setting("meta_ig_token")
    return GRAPH, setting("meta_page_token")


def refresh_page_info(force=False):
    """Pick up a renamed Facebook Page (or Instagram username) without reconnecting. Checked at most every 6 hours."""
    if not setting("meta_page_token") or not setting("meta_fb_page_id"):
        return
    try:
        if not force and time.time() - float(setting("meta_info_at") or 0) < 6 * 3600:
            return
    except ValueError:
        pass
    try:
        d = _graph("GET", f"/{setting('meta_fb_page_id')}",
                   {"fields": "name,instagram_business_account{id,username}", "access_token": setting("meta_page_token")})
        if d.get("name"):
            set_setting("meta_page_name", d["name"])
        ig = d.get("instagram_business_account") or {}
        if ig.get("username") and not setting("meta_ig_token"):
            set_setting("meta_ig_name", ig["username"])
        set_setting("meta_info_at", time.time())
    except RuntimeError:
        pass


def refresh_ig_token():
    """Instagram-only tokens last 60 days; renew quietly once they are 30+ days old."""
    tok, at = setting("meta_ig_token"), setting("meta_ig_token_at")
    if not tok or not at:
        return
    try:
        age = time.time() - float(at)
    except ValueError:
        return
    if age < 30 * 86400:
        return
    try:
        r = _graph("GET", "/refresh_access_token", {"grant_type": "ig_refresh_token", "access_token": tok}, base=IG_GRAPH)
        if r.get("access_token"):
            set_setting("meta_ig_token", r["access_token"])
            set_setting("meta_ig_token_at", time.time())
    except RuntimeError:
        pass


def _pub_sig(fid):
    from flask import current_app
    import hmac
    return hmac.new(current_app.secret_key.encode() if isinstance(current_app.secret_key, str) else current_app.secret_key,
                    fid.encode(), hashlib.sha256).hexdigest()[:24]


def site_base():
    env = os.environ.get("SITE_URL")
    if env:
        return env.rstrip("/")
    if has_request_context():
        return request.host_url.rstrip("/")
    return (setting("site_seen") or "").rstrip("/")


def pub_img_url(fid, fit=False):
    base = site_base()
    return f"{base}/pubimg/{_pub_sig(fid)}/{fid}.jpg" + ("?fit=45" if fit else "")


@bp.get("/pubimg/<sig>/<path:fid>.jpg")
def pubimg(sig, fid):
    """Public (signed) picture link. Instagram and Facebook download the photo from here when you post."""
    import hmac
    if not hmac.compare_digest(sig, _pub_sig(fid)):
        abort(404)
    path = fetch_file(fid)
    if not path:
        abort(404)
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        w, h = im.size
        if request.args.get("fit") == "45":      # swipe posts: fit every photo inside the same 4:5 frame, nothing cropped
            cw, ch = 1080, 1350
            sc = min(cw / w, ch / h)
            im = im.resize((max(1, int(w * sc)), max(1, int(h * sc))))
            canvas = Image.new("RGB", (cw, ch), "white")
            canvas.paste(im, ((cw - im.size[0]) // 2, (ch - im.size[1]) // 2))
            buf = io.BytesIO(); canvas.save(buf, "JPEG", quality=90)
            return Response(buf.getvalue(), mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})
        ratio = w / h
        lo, hi = 0.8, 1.91                      # Instagram accepts 4:5 up to 1.91:1; pad with white otherwise
        if ratio < lo:
            nw = int(h * lo); canvas = Image.new("RGB", (nw, h), "white"); canvas.paste(im, ((nw - w) // 2, 0)); im = canvas
        elif ratio > hi:
            nh = int(w / hi); canvas = Image.new("RGB", (w, nh), "white"); canvas.paste(im, (0, (nh - h) // 2)); im = canvas
        if im.size[0] > 1440:
            im = im.resize((1440, int(im.size[1] * 1440 / im.size[0])))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
        return Response(buf.getvalue(), mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})
    except Exception:
        return send_file(path, mimetype="image/jpeg", max_age=3600)


# ---- automatic captions (no writing needed). Each kind of product has its own tone.
# Hijabs and jilbabs share one "modest wear" voice; textiles, watches, shoes, caps, eyewear and other accessories each have theirs.
_TONES = {
    "modest": ([
        "✨ New hijab alert! Meet the {name}.", "🧕🏽 Modesty, elegance, comfort: the {name} is here.",
        "💫 Your next favourite piece: {name}.", "🌸 Soft, stylish and beautiful: {name}.",
        "🤍 Wrap yourself in grace with the {name}.", "👑 Queens, meet the {name}.",
        "🌷 Simple. Classy. Yours: {name}.", "💖 Look elegant every single day with the {name}.",
        "🌟 Fresh drop: the {name}.", "🖤 Elegance in every stitch: {name}.",
        "✨ Dress modestly, look stunning: {name}.", "🤲🏽 Graceful from head to toe: {name}."],
        ["Limited stock, so don't wait!", "Order yours today. 💛", "Tap, choose your colour and order! 💛",
         "Beautiful quality you can trust. 💛", "Ready for delivery anywhere in Nigeria. 🚚",
         "Grab yours before it sells out! 🏃🏽‍♀️", "Your size, your colour, your style. 💛", "Loved by our customers. Be next! 🌟"],
        ["#ADFashion #ADFashionHijabs #Hijab #Jilbab #ModestFashion #BauchiFashion #NigeriaFashion #HijabStyle",
         "#ADFashionHijabs #ModestStyle #HijabFashion #HijabNigeria #ModestWear #ArewaFashion #BauchiBusiness",
         "#ADFashion #AbayaStyle #JilbabStyle #HijabLover #ModestOutfit #NorthernNigeria #FashionNG"]),
    "fabric": ([
        "🧵 Premium fabric just arrived: {name}.", "✂️ Tailors and stylists, look at this: {name}.",
        "🪡 Sew something beautiful with our {name}.", "🌟 Rich texture, rich colour: {name} is in stock.",
        "👔 Your next outfit starts here: {name}.", "🧶 Feel the quality: {name}.",
        "📏 Cut to the length you need: {name}.", "🎉 Owambe ready? Get the {name}.",
        "💎 Fabric that makes the tailor smile: {name}.", "🛍️ Fresh roll in store: {name}.",
        "🧵 Looking for quality material? Meet {name}.", "✨ Make your own style with {name}."],
        ["Tell us how many yards you need and we'll cut it. ✂️", "Buy by the yard or by the trouser. 📏",
         "Perfect for weddings, Sallah and special days. 🎊", "Quality your tailor will love. 🧵",
         "Order the exact length you want. 💛", "Delivered to you anywhere in Nigeria. 🚚",
         "Stock moves fast, so order early! 🏃🏽", "Plenty of colours and lengths to choose from. 🎨"],
        ["#ADFashion #Fabric #Textiles #NigeriaFabrics #BauchiFabrics #ShaddaFabric #TailorsOfNigeria #FabricStore",
         "#ADFashion #SwissFabric #Cashmere #OwambeFabric #FabricVendor #SewingNigeria #ArewaFabrics",
         "#ADFashion #FabricByTheYard #AsoEbi #NigerianTailors #QualityFabric #BauchiBusiness"]),
    "watch": ([
        "⌚ Time to shine: the {name}.", "🕰️ Class on your wrist: {name}.", "✨ Be on time, in style: {name}.",
        "⌚ Every second looks good with the {name}.", "💎 Upgrade your wrist: {name}.",
        "🔥 Clean, sharp and ready: {name}.", "🕒 Your wrist deserves this: {name}.",
        "⌚ A gift that never goes out of style: {name}.", "👑 Wear confidence: {name}.",
        "🌟 New on the shelf: {name}.", "⏱️ Details that stand out: {name}.", "🎁 Looking for a gift? Meet the {name}."],
        ["Perfect for gifting. 🎁", "Limited pieces, so don't wait! ⏳", "Order today and wear it tomorrow. 💛",
         "Looks even better in person. 😍", "Ready for delivery anywhere in Nigeria. 🚚",
         "Message us to order. 💬", "Make it yours today. ⌚", "Be the one they ask about. 😎"],
        ["#ADFashion #Watches #WristWatch #WatchLover #MensWatch #WatchesNigeria #BauchiBusiness #Style",
         "#ADFashion #WatchStyle #WristGame #LuxuryLook #WatchCollection #NigeriaFashion #GiftIdeas",
         "#ADFashion #WatchOfTheDay #TimeIsStyle #FashionAccessories #ShopLocal #BauchiState"]),
    "shoes": ([
        "👞 Step out in style: the {name}.", "👠 Walk in with confidence: {name}.", "✨ Comfort you can feel with every step: {name}.",
        "🔥 New pair alert: {name}.", "👟 Your feet deserve the {name}.", "💫 Fresh pair, fresh vibes: {name}.",
        "🌟 Just in: the {name}.", "👞 Clean look, comfortable fit: {name}.", "💖 From work to weekend: {name}.",
        "🛍️ New pair in store: {name}.", "👑 Walk like you own the place: {name}.", "✨ Meet your next favourite pair: {name}."],
        ["Choose your size and order! 💛", "Comfortable all day long. 😌", "Sizes go fast, so order now! 🏃🏽",
         "Tell us your size and we'll sort you out. 📏", "Ready for delivery anywhere in Nigeria. 🚚",
         "Message us to order. 💬", "Quality that lasts. 💛", "Make a statement, one step at a time. ✨"],
        ["#ADFashion #Shoes #FootwearNigeria #ShoeLover #ShoeGame #BauchiBusiness #NigeriaFashion #StepUp",
         "#ADFashion #NewShoes #ShoesOfInstagram #FashionNG #ShopLocal #ShoeStore",
         "#ADFashion #FootwearFashion #ShoeStyle #ComfortAndStyle #BauchiState"]),
    "caps": ([
        "🧢 Top it off with the {name}.", "😎 Cap game strong: {name}.", "🔥 New cap alert: {name}.",
        "🧢 Keep it clean and simple: {name}.", "✨ The finishing touch: {name}.", "🌟 Fresh on the shelf: {name}.",
        "🧢 Cool, simple, comfortable: {name}.", "👑 Wear it your way: {name}.", "💫 Your outfit needs this: {name}.",
        "☀️ Sun out, cap on: {name}.", "🎯 Easy to style, easy to love: {name}.", "🛍️ New in store: {name}."],
        ["Choose yours and order today! 💛", "Goes with almost everything. 😉", "Ready for delivery anywhere in Nigeria. 🚚",
         "Limited stock, so don't wait! ⏳", "Message us to order. 💬", "Great as a gift too. 🎁",
         "Comfortable all day long. 😌", "Pick your colour and grab it! 🎨"],
        ["#ADFashion #Caps #CapGame #CapStyle #FashionNG #BauchiBusiness #StreetStyle",
         "#ADFashion #NewCap #HeadWear #StyleNigeria #ShopLocal #BauchiState",
         "#ADFashion #CapCollection #MensStyle #WomenStyle #FashionAccessories"]),
    "eyewear": ([
        "🕶️ Cool looks start here: the {name}.", "😎 See and be seen: {name}.", "✨ Add a little swag: {name}.",
        "🌞 Sun out? Put on the {name}.", "🕶️ Frame your style with {name}.", "🔥 New shades alert: {name}.",
        "💎 Small piece, big style: {name}.", "🌟 Fresh in store: {name}.", "😎 Bring the confidence: {name}.",
        "🕶️ Your new favourite pair: {name}.", "👑 Style it up: {name}.", "🛍️ Just landed: {name}."],
        ["Perfect for the sun and for the style. ☀️", "Order yours today! 💛", "Limited pieces available. ⏳",
         "Ready for delivery anywhere in Nigeria. 🚚", "Message us to order. 💬", "A great gift idea. 🎁",
         "Looks good on everyone. 😎", "Complete your look today. ✨"],
        ["#ADFashion #Goggles #Sunglasses #Shades #EyewearStyle #BauchiBusiness #FashionNG",
         "#ADFashion #SunglassesStyle #CoolLook #ShopLocal #FashionAccessories #BauchiState",
         "#ADFashion #NewShades #StyleGoals #EyewearFashion #NigeriaFashion"]),
    "accessory": ([
        "✨ The finishing touch you need: {name}.", "💎 Small details, big impact: {name}.", "🛍️ New in store: {name}.",
        "🌟 Complete your look with the {name}.", "🔥 Just landed: {name}.", "🎁 Treat yourself to the {name}.",
        "💫 Style it your way: {name}.", "👑 Little extra, lots of class: {name}.", "🌸 Meet the {name}.",
        "✨ You'll want this one: {name}.", "💖 Add some magic to your outfit: {name}.", "🌷 New arrival: {name}."],
        ["Order yours today! 💛", "A perfect gift idea. 🎁", "Limited stock, so don't wait! ⏳",
         "Ready for delivery anywhere in Nigeria. 🚚", "Message us to order. 💬", "Quality you can trust. 💛",
         "Looks even better in person. 😍", "Grab it before it's gone! 🏃🏽"],
        ["#ADFashion #FashionAccessories #StyleNigeria #BauchiBusiness #ShopLocal #NewArrival #GiftIdeas",
         "#ADFashion #AccessoriesLover #FashionNG #BauchiState #StyleGoals #ShopSmall",
         "#ADFashion #OutfitDetails #FashionFinds #NigeriaFashion #BauchiFashion"]),
    "default": ([
        "✨ Just in: {name}!", "🛍️ Now available at AD FASHION: {name}.", "💫 Don't miss the {name}.",
        "🌟 Look what's new: {name}.", "💖 Treat yourself to the {name}.", "🔥 New in store: {name}.",
        "🌸 Meet the {name}.", "👑 Style that speaks: {name}.", "🤍 Quality you can feel: {name}.",
        "🌷 Fresh arrival: {name}.", "🎉 Something new for you: {name}.", "💎 Just landed: {name}."],
        ["Limited stock, so don't wait!", "Order yours today. 💛", "Quality you can trust. 💛",
         "Ready for delivery anywhere in Nigeria. 🚚", "Message us to order. 💬", "Be among the first to get it! 🌟",
         "Make it yours today. ✨", "Loved by our customers. Be next! 🌟"],
        ["#ADFashion #ADFashionHijabs #NewArrival #BauchiBusiness #ShopLocal #NigeriaFashion #BauchiFashion",
         "#ADFashion #FashionNG #ShopSmall #BauchiState #StyleNigeria #NewIn",
         "#ADFashion #FashionFinds #NigerianBusiness #ArewaFashion #ShopNow"]),
}
_ORDER_IG = ["📲 Order on our app (link in bio)", "🛒 Shop it on our app, link in bio", "📲 Tap the link in bio to order",
             "🛍️ Order from our app, link in bio"]
_ORDER_FB = ["📲 Order on our app: {site}", "🛒 Shop it on our app: {site}", "🛍️ Order here: {site}", "📲 Get yours on the app: {site}"]


def tone_of(p):
    """Which voice a product gets, from its category first, then its name."""
    def look(t):
        t = (t or "").lower()
        for key, pat in (("modest", r"hijab|jilbab|abaya|niqab|khimar|veil"), ("watch", r"watch"),
                         ("shoes", r"shoe|sandal|slipper|sneaker|heel"), ("caps", r"(^|[^a-z])caps?([^a-z]|$)"),
                         ("eyewear", r"goggle|sunglass|glasses|shades"),
                         ("fabric", r"textile|fabric|shadda|cashmere|swiss|osaka|lace|atamfa|ankara|material|brocade"),
                         ("accessory", r"accessor|^more$|\bmore\b|bag|perfume|jewel|ring|necklace|bracelet|scarf|belt")):
            if re.search(pat, t):
                return key
        return None
    try:
        sub = p["subcategory"]
    except (KeyError, IndexError):
        sub = ""
    return look(f"{p['category'] or ''} {sub or ''}") or look(p["name"]) or "default"


def _norm(t):
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def used_captions(pid, label):
    rows = db().execute("SELECT caption FROM social_posts WHERE product_id=? AND platform=? AND status='ok' AND caption!=''",
                        (pid, label)).fetchall()
    return {_norm(r[0]) for r in rows}


def _recent_styles(tone, label, limit=60):
    """Opening lines and closing lines used by the latest posts (any product) in this tone, newest first."""
    openers, enders, _ = _TONES[tone]
    ops, ens = [], []
    rows = db().execute("""SELECT s.caption, p.name FROM social_posts s JOIN products p ON p.id=s.product_id
                           WHERE s.platform=? AND s.status='ok' AND s.caption!='' ORDER BY s.id DESC LIMIT ?""", (label, limit)).fetchall()
    for cap, name in rows:
        oi = next((i for i, o in enumerate(openers) if cap.startswith(o.replace("{name}", name or ""))), None)
        if oi is None:
            continue
        ops.append(oi)
        ens.append(next((i for i, e in enumerate(enders) if e in cap), None))
    return ops, ens


def fresh_caption(p, plat, start=0):
    """A caption this product never used on this platform, and (when possible) with an opening and closing line
    that the latest posts of other products did not use."""
    label = "Facebook" if plat == "fb" else "Instagram"
    used = used_captions(p["id"], label)
    tone = tone_of(p)
    recent_o, recent_e = _recent_styles(tone, label)
    avoid_o, avoid_e = set(recent_o[:6]), set(recent_e[:4])
    openers, enders, _ = _TONES[tone]
    n, cap = start, ""
    for k in range(500):
        cap, oi, ei = _build(p, plat, n)
        fresh = _norm(cap) not in used
        varied = oi not in avoid_o and ei not in avoid_e
        if fresh and (varied or k >= 150):
            return cap
        n += 1
    return cap + "\n\n🗓️ " + time.strftime("%d %b %Y, %H:%M")


def _build(p, platform, shuffle=0):
    import random
    rnd = random.Random(f"{p['id']}-{shuffle}")
    tone = tone_of(p)
    openers, enders, tags = _TONES[tone]
    oi, ei = rnd.randrange(len(openers)), rnd.randrange(len(enders))
    lines = [openers[oi].replace("{name}", p["name"])]
    desc = re.sub(r"\s+", " ", (p["description"] or "").strip())
    if desc:
        lines.append(desc[:140].rstrip() + ("…" if len(desc) > 140 else ""))
    fp = fabric_prices(p["id"])
    if fp:
        lines.append("\n".join((f"• {money(v)} per {UNIT_WORD[k]}" if is_unit_key(k) else f"• {FABRIC_NAME[k]}: {money(v)}")
                               for k, v in fp.items()))
    elif p["price"]:
        lines.append(f"💰 {money(p['price'])}")
    pct = discount_pct()
    if pct:
        lines.append(f"🎉 {pct}% OFF right now!")
    lines.append(enders[ei])
    wa = CFG.get("WHATSAPP", "")
    site = site_base()
    if platform == "ig":
        lines.append(rnd.choice(_ORDER_IG) + (f" or WhatsApp {wa}" if wa else ""))
        lines.append("📍 Bauchi, Nigeria · Delivery nationwide")
        tag = re.sub(r"[^A-Za-z0-9]", "", p["name"])
        lines.append(rnd.choice(tags) + (f" #{tag}" if tag else ""))
    else:
        lines.append(rnd.choice(_ORDER_FB).format(site=site))
        if wa:
            lines.append(f"💬 WhatsApp: https://wa.me/{wa}")
        lines.append("📍 Bauchi, Nigeria · Delivery nationwide")
    return "\n\n".join(lines), oi, ei


def make_caption(p, platform, shuffle=0):
    return _build(p, platform, shuffle)[0]


def post_facebook(img_urls, caption):
    if isinstance(img_urls, str):
        img_urls = [img_urls]
    page, tok = setting("meta_fb_page_id"), setting("meta_page_token")
    if len(img_urls) == 1:
        r = _graph("POST", f"/{page}/photos", {"url": img_urls[0], "caption": caption, "access_token": tok})
        return r.get("post_id") or r.get("id", "")
    ids = []
    for u in img_urls:                           # upload every photo unpublished, then attach them all to ONE post
        ids.append(_graph("POST", f"/{page}/photos", {"url": u, "published": "false", "access_token": tok})["id"])
    r = _graph("POST", f"/{page}/feed", {"message": caption, "access_token": tok,
                                          "attached_media": json.dumps([{"media_fbid": i} for i in ids])})
    return r.get("id", "")


def _ig_wait(cid, tok, base, tries=15, pause=2):
    for _ in range(tries):                       # wait until Instagram has fetched the picture or video
        st = _graph("GET", f"/{cid}", {"fields": "status_code", "access_token": tok}, base=base).get("status_code", "FINISHED")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError("Instagram could not use this picture or video.")
        time.sleep(pause)


def post_instagram(img_urls, caption):
    if isinstance(img_urls, str):
        img_urls = [img_urls]
    base, tok = _ig_target()
    ig = setting("meta_ig_id")
    if len(img_urls) == 1:
        cid = _graph("POST", f"/{ig}/media", {"image_url": img_urls[0], "caption": caption, "access_token": tok}, base=base)["id"]
    else:                                        # swipe post (carousel): one container per photo, then one for the post
        kids = []
        for u in img_urls:
            kids.append(_graph("POST", f"/{ig}/media", {"image_url": u, "is_carousel_item": "true", "access_token": tok}, base=base)["id"])
        for k in kids:
            _ig_wait(k, tok, base)
        cid = _graph("POST", f"/{ig}/media", {"media_type": "CAROUSEL", "children": ",".join(kids),
                                              "caption": caption, "access_token": tok}, base=base)["id"]
    _ig_wait(cid, tok, base)
    return _graph("POST", f"/{ig}/media_publish", {"creation_id": cid, "access_token": tok}, base=base).get("id", "")


def share_photos(pid):
    return [x["file_id"] for x in product_photos(pid)][:10]


def do_share(p, picked, want, caps=None, auto=0):
    """Post one product (all chosen photos) to the wanted platforms. Returns a list of messages."""
    rdy, caps, out, pid = social_ready(), caps or {}, [], p["id"]
    for plat, fn in (("fb", post_facebook), ("ig", post_instagram)):
        if plat not in want or not rdy[plat]:
            continue
        label = "Facebook" if plat == "fb" else "Instagram"
        caption = (caps.get(plat) or "").strip()
        if not caption:
            caption = fresh_caption(p, plat)
        elif _norm(caption) in used_captions(pid, label):
            caption = fresh_caption(p, plat)
            out.append(f"ℹ️ That {label} caption was used before, so a new one was written.")
        caption = caption[:2100]
        urls = [pub_img_url(f, fit=(plat == "ig" and len(picked) > 1)) for f in picked]
        try:
            rid = fn(urls, caption)
            db().execute("INSERT INTO social_posts(product_id,platform,status,remote_id,caption,auto) VALUES(?,?,?,?,?,?)",
                         (pid, label, "ok", str(rid), caption, auto))
            out.append(f"✅ Posted to {label}" + (f" ({len(picked)} photos)" if len(picked) > 1 else ""))
        except Exception as e:
            db().execute("INSERT INTO social_posts(product_id,platform,status,error,auto) VALUES(?,?,?,?,?)",
                         (pid, label, "failed", str(e)[:300], auto))
            out.append(f"❌ {label} failed: {e}")
        db().commit()
    return out


# ---- background sending: tapping Post only queues a job; the server finishes it even if you leave the app
JOBS_SYNC = False            # tests switch this on to run a job in the same request


def queue_job(kind, pid="", want=(), picked=(), caps=None, take=0):
    """Queue a post and start it in the background. Returns the job id, or None if the same post is already on its way."""
    from flask import current_app
    if db().execute("""SELECT 1 FROM share_jobs WHERE product_id=? AND kind=? AND status IN ('queued','working')
                       AND created_at>=datetime('now','-10 minutes')""", (pid, kind)).fetchone():
        return None
    if not os.environ.get("SITE_URL") and has_request_context():
        set_setting("site_seen", request.host_url.rstrip("/"))
    cur = db().execute("INSERT INTO share_jobs(product_id,kind,want,picked,caps,take) VALUES(?,?,?,?,?,?)",
                       (pid, kind, json.dumps(list(want)), json.dumps(list(picked)), json.dumps(caps or {}), take))
    db().commit()
    jid, app = cur.lastrowid, current_app._get_current_object()
    if JOBS_SYNC:
        run_job(app, jid)
    else:
        threading.Thread(target=run_job, args=(app, jid), daemon=True, name=f"share-{jid}").start()
    return jid


def run_job(flask_app, jid):
    with flask_app.app_context():
        try:
            j = db().execute("SELECT * FROM share_jobs WHERE id=?", (jid,)).fetchone()
            if not j:
                return
            db().execute("UPDATE share_jobs SET status='working' WHERE id=?", (jid,))
            db().commit()
            want, picked, caps = json.loads(j["want"]), json.loads(j["picked"]), json.loads(j["caps"])
            p = db().execute("SELECT * FROM products WHERE id=?", (j["product_id"],)).fetchone() if j["product_id"] else None
            pushed = False
            if j["kind"] == "next":
                p, msgs = run_auto_post("manual")
                pushed = True                                    # run_auto_post already tells the admin
            elif not p:
                msgs = ["❌ That product no longer exists."]
            elif j["kind"] == "reel":
                msgs = do_reel(p, make_reel(p, j["take"] or 0), want, caps)
            else:
                msgs = do_share(p, picked, want, caps)
            good = [m for m in msgs if m.startswith("✅")]
            bad = [m for m in msgs if m.startswith("❌")]
            status = "done" if good and not bad else ("failed" if not good else "partial")
            db().execute("UPDATE share_jobs SET status=?, result=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
                         (status, json.dumps(msgs), jid))
            db().commit()
            if not pushed and p:
                push_admins(("✅ Posted: " if status == "done" else "⚠️ Post problem: ") + p["name"],
                            " · ".join(m for m in msgs if not m.startswith("ℹ️"))[:180], url="/admin/share", tag="share")
        except Exception as e:
            try:
                db().execute("UPDATE share_jobs SET status='failed', result=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
                             (json.dumps([f"❌ {e}"]), jid))
                db().commit()
                push_admins("⚠️ Post failed", str(e)[:150], url="/admin/share", tag="share")
            except Exception:
                pass
        finally:
            try:
                db().close()
            except Exception:
                pass


def _job_sig():
    r = db().execute("SELECT id, status FROM share_jobs ORDER BY id DESC LIMIT 1").fetchone()
    return f"{r['id']}:{r['status']}" if r else "0"


@bp.get("/admin/share/pulse")
@admin_required
def admin_share_pulse():
    return {"sig": _job_sig()}


def recent_jobs(limit=5):
    rows = db().execute("""SELECT j.*, p.name FROM share_jobs j LEFT JOIN products p ON p.id=j.product_id
                           WHERE j.created_at>=datetime('now','-1 day') ORDER BY j.id DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        try:
            msgs = json.loads(r["result"] or "[]")
        except ValueError:
            msgs = []
        out.append({"id": r["id"], "name": r["name"] or "Next product", "kind": r["kind"], "status": r["status"],
                    "msgs": msgs, "at": (r["finished_at"] or r["created_at"] or "")[11:16]})
    return out


# ---- automatic daily posting (morning / afternoon / evening, Nigeria time)
LAGOS = timezone(timedelta(hours=1))
AUTO_DEFAULT_TIMES = "08:00,13:00,19:00"
AUTO_GRACE_MIN = 120          # a slot is still honoured up to 2 hours late (e.g. after a restart)
AUTO_QUIET_BEFORE = 90        # you already posted by hand within 90 minutes before the slot: skip it


def auto_times():
    out = []
    for t in (setting("auto_times", AUTO_DEFAULT_TIMES) or "").split(","):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", t)
        if m and int(m[1]) < 24 and int(m[2]) < 60:
            out.append(f"{int(m[1]):02d}:{m[2]}")
    return sorted(set(out))[:6]


def pick_auto_product():
    """The product that has waited longest since it was last posted, never the one posted just before."""
    last = setting("auto_last_pid")
    rows = db().execute("""SELECT p.*, (SELECT MAX(created_at) FROM social_posts s WHERE s.product_id=p.id AND s.status='ok') AS lp
                           FROM products p WHERE p.active=1 ORDER BY (lp IS NOT NULL), lp, p.rowid DESC""").fetchall()
    elig = [r for r in rows if share_photos(r["id"])]
    nxt = [r for r in elig if r["id"] != last]
    return (nxt or elig or [None])[0]


def run_auto_post(slot_label="", force=False, reel=False):
    """Post the next product now (as a Reel when asked). Returns (product or None, messages)."""
    rdy = social_ready()
    want = [pl for pl in ("fb", "ig") if setting("auto_" + pl, "1") == "1" and rdy[pl]]
    if not want:
        return None, ["Auto-post: connect Facebook or Instagram first."]
    if not site_base():
        return None, ["Auto-post: the app address is unknown. Set SITE_URL in Railway or open the Share page once."]
    p = pick_auto_product()
    if not p:
        return None, ["Auto-post: no active product with a photo."]
    picked = share_photos(p["id"])
    msgs = None
    if reel:
        try:
            msgs = do_reel(p, make_reel(p), want, auto=1)
        except Exception as e:                    # no video tool, no photo, ...: still post the photos so the slot is not lost
            msgs = [f"ℹ️ Reel not possible ({e}). Posted the photos instead."] + do_share(p, picked, want, auto=1)
    if msgs is None:
        msgs = do_share(p, picked, want, auto=1)
    set_setting("auto_last_pid", p["id"])
    ok = [m for m in msgs if m.startswith("✅")]
    bad = [m for m in msgs if m.startswith("❌")]
    title = ("🤖 Auto-posted: " if ok else "⚠️ Auto-post failed: ") + p["name"]
    body = " · ".join(m for m in msgs if not m.startswith("ℹ️") or "Reel not possible" in m)
    push_admins(title, body[:180], url="/admin/share", tag="autopost")
    return p, msgs


def auto_tick(flask_app, now=None):
    """Called every minute: run any slot that is due, was not already covered by a manual post, and not yet done today."""
    with flask_app.app_context():
        try:
            if setting("auto_on", "0") != "1":
                return []
            now = now or datetime.now(LAGOS)
            ran = []
            for t in auto_times():
                h, m = map(int, t.split(":"))
                slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
                key = f"auto_done_{slot:%Y-%m-%d}_{t}"
                if not (slot <= now < slot + timedelta(minutes=AUTO_GRACE_MIN)) or setting(key):
                    continue
                set_setting(key, "1")                                   # claim first so it can never fire twice
                cutoff = (slot - timedelta(minutes=AUTO_QUIET_BEFORE)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                if db().execute("SELECT 1 FROM social_posts WHERE status='ok' AND created_at>=?", (cutoff,)).fetchone():
                    ran.append((t, "skipped"))
                    continue
                is_last = t == auto_times()[-1]
                reel = setting("auto_reel", "0") == "1" and is_last
                jid = db().execute("INSERT INTO share_jobs(kind,status) VALUES(?,'working')", ("autoreel" if reel else "auto",)).lastrowid
                db().commit()
                p, msgs = run_auto_post(t, reel=reel)
                good = [m for m in msgs if m.startswith("✅")]
                bad = [m for m in msgs if m.startswith("❌")] or ([] if good else ["x"])
                db().execute("UPDATE share_jobs SET product_id=?, status=?, result=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
                             (p["id"] if p else "", "done" if good and not bad else ("partial" if good else "failed"), json.dumps(msgs), jid))
                db().commit()
                ran.append((t, p["id"] if p else "none"))
            return ran
        except Exception:
            return []
        finally:
            try:
                db().close()
            except Exception:
                pass


def start_autopost(flask_app):
    try:                                          # a restart kills running jobs: say so instead of showing "sending" forever
        c = sqlite3.connect(CFG["DB"])
        c.execute("UPDATE share_jobs SET status='failed', result=?, finished_at=CURRENT_TIMESTAMP WHERE status IN ('queued','working')",
                  (json.dumps(["❌ The app restarted while sending. Please tap Post again."]),))
        c.commit()
        c.close()
    except Exception:
        pass

    def loop():
        try:
            with flask_app.app_context():
                purge_reels(older_minutes=0)          # nothing from before a restart is kept
        except Exception:
            pass
        while True:
            time.sleep(15)
            auto_tick(flask_app)
    threading.Thread(target=loop, daemon=True, name="autopost").start()


@bp.post("/admin/share/auto")
@admin_required
def admin_share_auto():
    on = "1" if request.form.get("auto_on") else "0"
    times = [request.form.get(f"t{i}", "").strip() for i in (1, 2, 3)]
    good = [t for t in times if re.fullmatch(r"\d{1,2}:\d{2}", t)]
    set_setting("auto_times", ",".join(good) or AUTO_DEFAULT_TIMES)
    set_setting("auto_fb", "1" if request.form.get("auto_fb") else "0")
    set_setting("auto_ig", "1" if request.form.get("auto_ig") else "0")
    set_setting("auto_reel", "1" if request.form.get("auto_reel") else "0")
    set_setting("auto_on", on)
    if on == "1":                                  # do not fire slots that already passed today
        now = datetime.now(LAGOS)
        for t in auto_times():
            h, m = map(int, t.split(":"))
            if now.replace(hour=h, minute=m, second=0, microsecond=0) <= now:
                set_setting(f"auto_done_{now:%Y-%m-%d}_{t}", "1")
    flash("Auto-posting is ON. " + " · ".join(auto_times()) if on == "1" else "Auto-posting is OFF.")
    return redirect(url_for("web.admin_share"))


@bp.post("/admin/share/auto/run")
@admin_required
def admin_share_auto_run():
    if queue_job("next") is None:
        flash("A post is already being sent. Please wait a moment.")
    else:
        flash("📤 Sending the next product in the background. You can leave the app.")
    return redirect(url_for("web.admin_share"))


@bp.get("/admin/share")
@admin_required
def admin_share():
    refresh_ig_token()
    refresh_page_info(force=bool(request.args.get("refresh")))
    _jobs = recent_jobs()
    if not os.environ.get("SITE_URL"):
        set_setting("site_seen", request.host_url.rstrip("/"))
    rdy = social_ready()
    prods = db().execute("""SELECT p.*, (SELECT MAX(created_at) FROM social_posts s WHERE s.product_id=p.id AND s.status='ok') AS last_post
                            FROM products p WHERE p.active=1 ORDER BY (p.added_at IS NULL), p.added_at DESC, p.rowid DESC""").fetchall()
    recent = db().execute("""SELECT s.*, p.name FROM social_posts s LEFT JOIN products p ON p.id=s.product_id
                             ORDER BY s.id DESC LIMIT 12""").fetchall()
    return admin_page("Share", """<h2>Share to {{ 'Facebook & Instagram' if rdy.fb and rdy.ig else 'Facebook' if rdy.fb else 'Instagram' }}</h2>
{% if not (rdy.fb or rdy.ig) %}<div class="card" style="padding:14px;margin-bottom:12px"><b>Not connected yet.</b>
<p class="mut small">Connect your Facebook Page and Instagram once, then you can post any product in one tap.</p>
<a class="btn gold sm" href="{{ url_for('web.admin_share_connect') }}">Connect now</a></div>
{% else %}<p class="small">Connected:
{% if rdy.fb %}<span class="tag g">Facebook · {{ fb_name }}</span>{% endif %} {% if rdy.ig %}<span class="tag g">Instagram · @{{ ig_name }}</span>{% endif %}
· <a href="{{ url_for('web.admin_share', refresh=1) }}">↻ Refresh names</a> · <a href="{{ url_for('web.admin_share_connect') }}">Reconnect</a></p>{% endif %}
{% if jobs %}<div class="card" style="padding:12px;margin-bottom:12px" id="jobs"><b>📤 Sending</b>
{% for j in jobs %}<div class="small" style="margin-top:6px"><b>{{ j['name'] }}</b>{% if j['kind'] in ('reel','autoreel') %} 🎬{% endif %}{% if j['kind'] in ('auto','autoreel') %} 🤖{% endif %} · {{ j['at'] }}
{% if j['status'] in ('queued','working') %}<span style="color:#b26a00">⏳ sending…</span>{% elif j['status']=='done' %}<span style="color:#2a8a4a">✅ sent</span>{% elif j['status']=='partial' %}<span style="color:#b26a00">⚠️ partly sent</span>{% else %}<span style="color:#c0392b">❌ not sent</span>{% endif %}
{% for m in j['msgs'] %}<div class="mut">{{ m }}</div>{% endfor %}</div>{% endfor %}</div>
{% if busy %}<script>setTimeout(function(){location.reload()},4000);</script>{% endif %}{% endif %}
<script>(function(){var s0="{{ sig }}";setInterval(function(){fetch("{{ url_for('web.admin_share_pulse') }}",{credentials:"same-origin"}).then(function(r){return r.json()}).then(function(d){if(d.sig!==s0)location.reload()}).catch(function(){})},10000)})();</script>
{% if rdy.fb or rdy.ig %}<form method="post" action="{{ url_for('web.admin_share_auto') }}" class="card" style="padding:14px;margin-bottom:12px"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="row between"><b>🤖 Auto-post</b><label class="small"><input type="checkbox" name="auto_on" value="1" {{ 'checked' if auto_on else '' }}> {{ 'ON' if auto_on else 'OFF' }}</label></div>
<p class="mut small" style="margin:6px 0">At each time below, if you have not posted yourself, the app posts the product that has waited longest. Nigeria time.</p>
<div class="row" style="gap:8px;flex-wrap:nowrap">{% for t in times %}<input type="time" name="t{{ loop.index }}" value="{{ t }}" style="min-width:0">{% endfor %}</div>
<div class="small" style="margin:8px 0">{% if rdy.fb %}<label><input type="checkbox" name="auto_fb" value="1" {{ 'checked' if af else '' }}> Facebook</label> {% endif %}{% if rdy.ig %}<label style="margin-left:10px"><input type="checkbox" name="auto_ig" value="1" {{ 'checked' if ai else '' }}> Instagram</label>{% endif %}</div>
<label class="small" style="display:block;margin-bottom:8px"><input type="checkbox" name="auto_reel" value="1" {{ 'checked' if ar else '' }}> 🎬 Make the last post of the day a Reel (video with music)</label>
<div class="row"><button class="btn gold sm">Save</button>{% if nxt %}<a class="btn alt sm" href="{{ url_for('web.admin_share_reel', pid=nxt['id']) }}">🎬 Preview a Reel</a>{% endif %}</div></form>
<form method="post" action="{{ url_for('web.admin_share_auto_run') }}" style="margin:-4px 0 12px"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn alt sm">▶ Post the next product now</button></form>{% endif %}
<p class="mut small">Tap a product. The picture and caption are made for you automatically. Just press Post.</p>
{% for p in prods %}<div class="card" style="display:flex;gap:10px;align-items:center;padding:10px;margin-bottom:8px">
<a href="{{ url_for('web.admin_share_product', pid=p['id']) }}" style="display:flex;gap:12px;align-items:center;text-decoration:none;color:inherit;flex:1;min-width:0">
{% if p['photo_file_id'] %}<img src="{{ url_for('web.img', fid=p['photo_file_id']) }}" alt="" style="width:64px;height:64px;object-fit:cover;border-radius:10px;flex:none">{% endif %}
<div class="grow" style="min-width:0"><b>{{ p['name'] }}</b><div class="small mut">{{ display_price(p) }}</div>
{% if p['last_post'] %}<div class="small" style="color:#2a8a4a">✓ Posted {{ p['last_post'][:10] }}</div>{% endif %}</div><span class="btn alt sm">Share ›</span></a>
<a class="btn gold sm" href="{{ url_for('web.admin_share_reel', pid=p['id']) }}" title="Post as a Reel">🎬</a></div>{% endfor %}
{% if recent %}<h3 style="margin-top:18px">Recent posts</h3>{% for r in recent %}<div class="small" style="margin-bottom:4px">
{{ '✅' if r['status']=='ok' else '❌' }} {{ r['platform']|title }}{% if r['auto'] %} 🤖{% endif %} · {{ r['name'] or r['product_id'] }} · {{ r['created_at'][:16] }}{% if r['error'] %}<div class="mut">{{ r['error'] }}</div>{% endif %}</div>{% endfor %}{% endif %}""",
                      rdy=rdy, prods=prods, recent=recent, fb_name=setting("meta_page_name"), ig_name=setting("meta_ig_name"),
                      auto_on=setting("auto_on", "0") == "1", af=setting("auto_fb", "1") == "1", ai=setting("auto_ig", "1") == "1",
                      times=(auto_times() + ["", "", ""])[:3], ar=setting("auto_reel", "0") == "1", nxt=pick_auto_product(),
                      jobs=_jobs, busy=any(j["status"] in ("queued", "working") for j in _jobs), sig=_job_sig())


@bp.route("/admin/share/<pid>", methods=["GET", "POST"])
@admin_required
def admin_share_product(pid):
    p = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    rdy = social_ready()
    allphotos = share_photos(pid)
    if request.method == "POST":
        picked = [f for f in request.form.getlist("ph") if f in allphotos] or ([] if request.form.getlist("ph") else allphotos)
        picked = [f for f in allphotos if f in picked]          # keep the gallery order
        if not picked:
            flash("This product has no picture selected. Add or tick at least one photo.")
            return redirect(url_for("web.admin_share_product", pid=pid))
        want = [pl for pl in ("fb", "ig") if request.form.get("do_" + pl)]
        caps = {pl: request.form.get("cap_" + pl, "").strip() for pl in ("fb", "ig")}
        if not want:
            flash("Choose Facebook or Instagram first.")
            return redirect(url_for("web.admin_share_product", pid=pid))
        if queue_job("photo", pid, want, picked, caps) is None:
            flash("This product is already being sent. Please wait a moment.")
        else:
            flash("📤 Sending in the background. You can leave this page or the app. We will notify you when it is done.")
        return redirect(url_for("web.admin_share"))
    shuffle = to_int(request.args.get("n"), 0) or 0
    last = db().execute("SELECT MAX(created_at) FROM social_posts WHERE product_id=? AND status='ok'", (pid,)).fetchone()[0]
    return admin_page("Share " + p["name"], """<p><a href="{{ url_for('web.admin_share') }}">‹ All products</a></p>
<div class="card" style="padding:14px"><div class="row" style="gap:12px">{% if p['photo_file_id'] %}<img src="{{ url_for('web.img', fid=p['photo_file_id']) }}" alt="" style="width:96px;height:96px;object-fit:cover;border-radius:12px">{% endif %}
<div class="grow"><h3 style="margin:0">{{ p['name'] }}</h3><div class="mut small">{{ display_price(p) }}</div>
{% if last %}<div class="small" style="color:#b26a00">Already posted on {{ last[:10] }}</div>{% endif %}</div></div></div>
<form method="post" class="card" style="padding:14px;margin-top:10px"><input type="hidden" name="csrf" value="{{ csrf }}">
{% if photos %}<label class="l">Photos to post ({{ photos|length }}){% if photos|length > 1 %} · they go out as one swipe post{% endif %}</label>
<div style="display:flex;gap:8px;overflow-x:auto;padding-bottom:8px;margin-bottom:8px">{% for f in photos %}<label style="flex:none;position:relative;display:block">
<input type="checkbox" name="ph" value="{{ f }}" checked style="position:absolute;top:5px;left:5px;z-index:2;width:20px;height:20px">
<img src="{{ url_for('web.img', fid=f) }}" alt="" style="width:78px;height:78px;object-fit:cover;border-radius:10px;display:block"></label>{% endfor %}</div>{% endif %}
{% if rdy.fb %}<label class="l"><input type="checkbox" name="do_fb" value="1" checked> Facebook Page</label>
<textarea name="cap_fb" rows="7">{{ cap_fb }}</textarea>{% endif %}
<label class="l" style="margin-top:10px"><input type="checkbox" name="do_ig" value="1" {{ 'checked' if rdy.ig else 'disabled' }}> Instagram {{ '' if rdy.ig else '(not connected)' }}</label>
<textarea name="cap_ig" rows="9">{{ cap_ig }}</textarea>
<div class="row" style="margin-top:10px"><button class="btn gold">📣 Post now</button>
<a class="btn alt" href="{{ url_for('web.admin_share_product', pid=p['id'], n=shuffle+1) }}">🔄 New caption</a>
<a class="btn alt" href="{{ url_for('web.admin_share_reel', pid=p['id']) }}">🎬 As a Reel</a></div>
<p class="mut small">The caption was written for you. You can leave it as it is, or change anything before posting.</p></form>""",
                      p=p, rdy=rdy, last=last, shuffle=shuffle, photos=allphotos,
                      cap_fb=fresh_caption(p, "fb", shuffle), cap_ig=fresh_caption(p, "ig", shuffle))


@bp.route("/admin/share/connect", methods=["GET", "POST"])
@admin_required
def admin_share_connect():
    pages = []
    try:
        pages = json.loads(setting("meta_pages_tmp", "[]") or "[]")
    except Exception:
        pages = []
    if request.method == "POST":
        try:
            if request.form.get("act") == "choose":
                pick = next((x for x in pages if x["id"] == request.form.get("page")), None)
                if not pick:
                    raise RuntimeError("Please choose a page.")
                _finish_connect(pick)
                flash(f"Connected to {pick['name']}.")
                return redirect(url_for("web.admin_share"))
            if request.form.get("act") == "igonly":
                tok = request.form.get("ig_token", "").strip()
                if not tok:
                    raise RuntimeError("Please paste the Instagram access token.")
                me = _graph("GET", "/me", {"fields": "user_id,username", "access_token": tok}, base=IG_GRAPH)
                igid = str(me.get("user_id") or me.get("id") or "")
                if not igid:
                    raise RuntimeError("Instagram did not return your account. Check the token.")
                set_setting("meta_ig_token", tok)
                set_setting("meta_ig_token_at", time.time())
                set_setting("meta_ig_id", igid)
                set_setting("meta_ig_name", me.get("username", ""))
                flash(f"Instagram connected: @{me.get('username', '')}")
                return redirect(url_for("web.admin_share"))
            app_id, secret = request.form.get("app_id", "").strip(), request.form.get("app_secret", "").strip()
            tok = request.form.get("user_token", "").strip()
            if not (app_id and secret and tok):
                raise RuntimeError("Please fill in all three boxes.")
            lt = _graph("GET", "/oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                                         "client_secret": secret, "fb_exchange_token": tok})["access_token"]
            acc = _graph("GET", "/me/accounts", {"access_token": lt, "fields": "id,name,access_token", "limit": 25}).get("data", [])
            if not acc:
                raise RuntimeError("No Facebook Page found for this login. Make sure you are an admin of the page and allowed it in the permissions.")
            if len(acc) == 1:
                _finish_connect(acc[0])
                flash(f"Connected to {acc[0]['name']}.")
                return redirect(url_for("web.admin_share"))
            set_setting("meta_pages_tmp", json.dumps(acc))
            return redirect(url_for("web.admin_share_connect"))
        except RuntimeError as e:
            flash(f"Could not connect: {e}")
            return redirect(url_for("web.admin_share_connect"))
    return admin_page("Connect", """<p><a href="{{ url_for('web.admin_share') }}">‹ Back</a></p><h2>Connect Facebook &amp; Instagram</h2>
{% if pages %}<form method="post" class="card" style="padding:14px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="act" value="choose">
<label class="l">Which page is your brand page?</label>{% for pg in pages %}<label class="l"><input type="radio" name="page" value="{{ pg['id'] }}" required> {{ pg['name'] }}</label>{% endfor %}
<p><button class="btn gold">Use this page</button></p></form>
{% else %}<form method="post" class="card" style="padding:14px"><input type="hidden" name="csrf" value="{{ csrf }}">
<h3 style="margin-top:0">Facebook Page + Instagram <span class="tag g">recommended</span></h3>
<p class="mut small">One connection posts to your Facebook Page and to the Instagram account linked to it. Your App Secret is used once and is not saved.</p>
<label class="l">App ID</label><input name="app_id" required autocomplete="off">
<label class="l">App Secret</label><input name="app_secret" type="password" required autocomplete="off">
<label class="l">Access token (from Graph API Explorer)</label><textarea name="user_token" rows="3" required autocomplete="off"></textarea>
<p><button class="btn gold">Connect Facebook &amp; Instagram</button></p></form>
<details style="margin-top:14px"><summary class="mut small">Instagram only (no Facebook Page)</summary>
<form method="post" class="card" style="padding:14px;margin-top:8px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="act" value="igonly">
<p class="mut small">Paste the Instagram access token from the Meta developer dashboard (Instagram, API setup with Instagram login, Generate token). It renews itself.</p>
<label class="l">Instagram access token</label><textarea name="ig_token" rows="3" required autocomplete="off"></textarea>
<p><button class="btn gold">Connect Instagram</button></p></form></details>{% endif %}""", pages=pages)


def _finish_connect(pg):
    set_setting("meta_page_token", pg["access_token"])
    set_setting("meta_fb_page_id", pg["id"])
    set_setting("meta_page_name", pg.get("name", ""))
    ig_id, ig_name = "", ""
    try:
        d = _graph("GET", f"/{pg['id']}", {"fields": "instagram_business_account{id,username}", "access_token": pg["access_token"]})
        ig = d.get("instagram_business_account") or {}
        ig_id, ig_name = ig.get("id", ""), ig.get("username", "")
    except RuntimeError:
        pass
    set_setting("meta_ig_id", ig_id)
    set_setting("meta_ig_name", ig_name)
    set_setting("meta_pages_tmp", "[]")
    if not ig_id:
        flash("Facebook is connected, but no Instagram Business account is linked to this page yet.")


@bp.get("/admin/customers")
@admin_required
def admin_customers():
    rows = db().execute("""SELECT u.*, (SELECT COUNT(*) FROM orders o WHERE o.web_user_id=u.id) AS n
                           FROM web_users u ORDER BY u.id DESC LIMIT 300""").fetchall()
    return admin_page("Customers", """<h2>App customers ({{ rows|length }})</h2>
{% for u in rows %}<div class="card" style="padding:12px;margin-bottom:8px"><div class="row between"><a href="{{ url_for('web.admin_customer', uid=u['id']) }}" style="font-weight:700;color:inherit">{{ u['name'] }} ›</a><span class="tag g">{{ u['score'] }} pts</span></div>
<div class="small mut">{{ u['email'] }}{% if u['phone'] %} · {{ u['phone'] }}{% endif %} · {{ u['n'] }} order(s) · joined {{ u['created_at'][:10] }}</div>
<form method="post" action="{{ url_for('web.admin_points', uid=u['id']) }}" class="row" style="flex-wrap:nowrap;margin-top:8px"><input type="hidden" name="csrf" value="{{ csrf }}">
<input name="delta" placeholder="± points" inputmode="numeric" style="max-width:110px"><button class="btn alt sm">Adjust points</button></form></div>{% endfor %}""", rows=rows)


@bp.get("/admin/customers/<int:uid>")
@admin_required
def admin_customer(uid):
    u = db().execute("SELECT * FROM web_users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    orders = db().execute("SELECT * FROM orders WHERE web_user_id=? ORDER BY id DESC", (uid,)).fetchall()
    st = _user_stats(uid)
    wish = db().execute("""SELECT p.id,p.name,p.photo_file_id FROM web_wishlist w JOIN products p ON p.id=w.product_id
                           WHERE w.user_id=?""", (uid,)).fetchall()
    return admin_page(u["name"], """<p><a href="{{ url_for('web.admin_customers') }}">‹ All customers</a></p>
<div class="card" style="padding:16px"><div class="row" style="gap:14px"><div class="avatar">{{ u['name'][:1]|upper }}</div>
<div class="grow"><h2 style="margin:0">{{ u['name'] }}</h2><div class="small mut">Joined {{ u['created_at'][:10] }}</div></div><span class="tag g">{{ u['score'] }} pts</span></div>
<div class="small" style="margin-top:10px">📧 {{ u['email'] }}<br>📞 {{ u['phone'] or 'no phone' }}
{% if u['phone'] %} · <a href="tel:{{ u['phone'] }}">Call</a> · <a href="https://wa.me/{{ u['phone']|replace('+','') }}">WhatsApp</a>{% endif %}</div>
<div class="kpi" style="margin-top:12px"><div><span class="mut small">Orders</span><b>{{ st.orders }}</b></div><div><span class="mut small">Delivered</span><b>{{ st.delivered }}</b></div><div><span class="mut small">Wishlist</span><b>{{ st.wish }}</b></div></div>
<div class="row between" style="margin-top:8px"><span class="mut small">Total spent</span><b>{{ money(st.spent) }}</b></div>
<form method="post" action="{{ url_for('web.admin_points', uid=u['id']) }}" class="row" style="flex-wrap:nowrap;margin-top:10px"><input type="hidden" name="csrf" value="{{ csrf }}">
<input name="delta" placeholder="± points" inputmode="numeric" style="max-width:110px"><button class="btn alt sm">Adjust points</button></form></div>
{% if gallery %}<h3 style="margin-top:16px">Gallery (ordered or saved)</h3><div style="display:flex;gap:10px;overflow-x:auto;padding-bottom:6px">{% for g in gallery %}
<a href="{{ url_for('web.admin_product', pid=g['id']) }}" style="flex:none;width:90px;text-decoration:none;color:inherit"><img src="{{ url_for('web.img', fid=g['photo_file_id']) }}" alt="" style="width:90px;height:90px;object-fit:cover;border-radius:12px"><div class="small" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ g['name'] }}</div></a>{% endfor %}</div>{% endif %}
<h3 style="margin-top:16px">Orders ({{ orders|length }})</h3>
{% for o in orders %}<a class="card" href="{{ url_for('web.admin_order', oid=o['id']) }}" style="display:block;padding:12px;margin-bottom:8px;text-decoration:none;color:inherit">
<div class="row between"><b>#AD-{{ '%05d'|format(o['id']) }}</b><span class="tag {{ 'ok' if o['status']=='DELIVERED' }}">{{ status_label.get(o['status'], o['status']) }}</span></div>
<div class="row between small mut"><span>{{ o['created_at'][:10] }}{% if o['points_awarded'] %} · +{{ o['points_awarded'] }} pts{% endif %}</span><b>{{ money(o['total']) }}</b></div></a>
{% else %}<p class="mut">No orders yet.</p>{% endfor %}
{% if wish %}<h3 style="margin-top:16px">Wishlist</h3><p class="small">{% for w in wish %}{{ w['name'] }}{{ ', ' if not loop.last }}{% endfor %}</p>{% endif %}""",
                      u=u, orders=orders, st=st, wish=wish, gallery=_user_gallery(uid, 24))


@bp.post("/admin/customers/<int:uid>/points")
@admin_required
def admin_points(uid):
    delta = to_int(request.form.get("delta"), 0)
    if delta:
        db().execute("UPDATE web_users SET score=MAX(0, score+?) WHERE id=?", (delta, uid))
        db().commit()
    return redirect(request.referrer or url_for("web.admin_customers"))


@bp.errorhandler(403)
def forbidden(_e):
    return page("Not allowed", "<h2>Not allowed</h2><p class='mut'>You don't have access to this page.</p>"), 403


@bp.errorhandler(404)
def not_found(_e):
    return page("Not found", "<h2>Page not found</h2><p><a class='btn gold' href='/'>Go home</a></p>"), 404


# ================================================================== registration into the bot's Flask app
def register(flask_app, cfg):
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
    start_autopost(flask_app)


# ================================================================== REELS (video + music, made by the app itself)
# A short vertical video (9:16) of the product: intro card, every photo with a slow zoom, an order card, and original
# music written in code (so there are no copyright problems). Posted to Instagram Reels and the Facebook Page.
REEL_W, REEL_H, REEL_FPS = 1080, 1920, 30
REEL_GOLD, REEL_INK = (212, 169, 74), (13, 13, 13)
REEL_MAX_BYTES = 9_500_000
MUSIC_SR = 44100


class ReelError(RuntimeError):
    pass


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


def _reel_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(CFG["DB"])), "reels")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- music
def _music_style(tone):
    return {"modest": "soft", "fabric": "lively"}.get(tone, "bold" if tone in ("watch", "shoes", "caps", "eyewear", "accessory") else "lively")


def make_music(style, seconds, seed, path):
    """Write an original instrumental (WAV). Styles: soft (elegant), lively (afro groove), bold (modern beat)."""
    import numpy as np
    import random as _random
    import wave
    sr = MUSIC_SR
    rnd = _random.Random(seed)
    rng = np.random.default_rng(rnd.randrange(1 << 30))
    n = int(sr * (seconds + 0.5))
    melodic, drums = np.zeros(n), np.zeros(n)

    def hz(m):
        return 440.0 * 2 ** ((m - 69) / 12)

    def put(buf, arr, at, gain=1.0):
        i = int(at * sr)
        if i >= n:
            return
        j = min(n, i + len(arr))
        buf[i:j] += arr[: j - i] * gain

    def tone(m, dur, kind):
        t = np.arange(int(sr * dur)) / sr
        f = hz(m)
        if kind == "pluck":
            w = np.sin(2 * np.pi * f * t) + 0.45 * np.sin(2 * np.pi * 2 * f * t) + 0.2 * np.sin(2 * np.pi * 3 * f * t)
            env = np.exp(-t * 5.5) * np.minimum(1, t / 0.004)
        elif kind == "bell":
            w = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2.76 * f * t) + 0.15 * np.sin(2 * np.pi * 5.4 * f * t)
            env = np.exp(-t * 3.2) * np.minimum(1, t / 0.003)
        elif kind == "pad":
            w = sum(np.sin(2 * np.pi * f * (1 + d) * t) for d in (-0.003, 0, 0.003)) / 3 + 0.25 * np.sin(2 * np.pi * 2 * f * t)
            env = np.minimum(1, t / 0.4) * np.minimum(1, np.maximum(0, (dur - t)) / 0.4)
        elif kind == "bass":
            w = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3 * f * t)
            env = np.exp(-t * 3.0) * np.minimum(1, t / 0.01)
        else:  # "sub": 808 with a quick pitch drop, saturated so small phone speakers can hear it
            ff = f * (1 + 1.2 * np.exp(-t * 28))
            w = np.tanh(2.2 * np.sin(2 * np.pi * np.cumsum(ff) / sr))
            env = np.exp(-t * 2.0) * np.minimum(1, t / 0.003)
        return w * env

    def kick():
        t = np.arange(int(sr * 0.32)) / sr
        return np.sin(2 * np.pi * np.cumsum(48 + 120 * np.exp(-t * 32)) / sr) * np.exp(-t * 9)

    def hat(d=0.05):
        t = np.arange(int(sr * d)) / sr
        x = rng.standard_normal(len(t))
        return (x - np.convolve(x, np.ones(6) / 6, "same")) * np.exp(-t * 80)

    def clap():
        t = np.arange(int(sr * 0.22)) / sr
        x = rng.standard_normal(len(t))
        x = np.convolve(x, np.ones(3) / 3, "same")
        env = np.exp(-t * 22) + 0.7 * np.exp(-np.maximum(0, t - 0.012) * 40) * (t > 0.012) + 0.5 * np.exp(-np.maximum(0, t - 0.024) * 40) * (t > 0.024)
        return x * env

    def snare():
        t = np.arange(int(sr * 0.2)) / sr
        return (rng.standard_normal(len(t)) * 0.7 + np.sin(2 * np.pi * 185 * t) * 0.5) * np.exp(-t * 20)

    if style == "soft":
        bpm, prog = 76, [("Am", [57, 60, 64]), ("F", [53, 57, 60]), ("C", [60, 64, 67]), ("G", [55, 59, 62])]
    elif style == "lively":
        bpm, prog = 104, [("Dm", [50, 53, 57]), ("Bb", [46, 50, 53]), ("F", [53, 57, 60]), ("C", [48, 52, 55])]
    else:
        bpm, prog = 92, [("Am", [57, 60, 64]), ("F", [53, 57, 60]), ("C", [60, 64, 67]), ("G", [55, 59, 62])]
    step = 60.0 / bpm / 4
    bar = step * 16
    bars = int(seconds / bar) + 1
    penta = [69, 72, 74, 76, 79, 81]
    for b in range(bars):
        name, ch = prog[b % 4]
        t0 = b * bar
        if style == "soft":
            for m in ch:
                put(melodic, tone(m - 12, bar + 0.4, "pad"), t0, 0.16)
            pat = [rnd.choice(ch) + 12 for _ in range(8)]
            for k, m in enumerate(pat):
                put(melodic, tone(m + (12 if rnd.random() < 0.25 else 0), 1.2, "bell" if k % 4 == 0 else "pluck"), t0 + k * 2 * step, 0.30)
            for s_ in (0, 8):
                put(drums, kick(), t0 + s_ * step, 0.45)
            for s_ in (4, 12):
                put(drums, hat(0.09), t0 + s_ * step, 0.22)
            for s_ in range(0, 16, 2):
                put(drums, hat(), t0 + s_ * step, 0.07)
        elif style == "lively":
            root = ch[0] - 12
            for s_ in (0, 6, 10):
                put(drums, kick(), t0 + s_ * step, 0.6)
            for s_ in (4, 12):
                put(drums, clap(), t0 + s_ * step, 0.45)
            for s_ in range(16):
                put(drums, hat(0.04 if s_ % 2 else 0.06), t0 + s_ * step, 0.16 if s_ % 4 == 0 else 0.09)
            for s_ in (0, 3, 6, 10, 13):
                put(melodic, tone(root, step * 3, "bass"), t0 + s_ * step, 0.55)
            for s_ in (2, 7, 10, 14):
                for m in ch:
                    put(melodic, tone(m + 12, 0.5, "pluck"), t0 + s_ * step, 0.12)
            for s_ in sorted(rnd.sample(range(16), 5)):
                put(melodic, tone(rnd.choice(ch) + 24, 0.5, "bell"), t0 + s_ * step, 0.11)
        else:
            root = ch[0] - 24
            for s_ in (0, 7, 10):
                put(drums, kick(), t0 + s_ * step, 0.55)
            put(drums, clap(), t0 + 8 * step, 0.5)
            for s_ in range(16):
                if rnd.random() < 0.9:
                    put(drums, hat(0.035), t0 + s_ * step, 0.14 if s_ % 2 == 0 else 0.07)
            for s_ in (0, 10):
                put(melodic, tone(root + 12, step * 8, "sub"), t0 + s_ * step, 0.62)
            for s_ in sorted(rnd.sample(range(16), 6)):
                put(melodic, tone(rnd.choice(penta), 0.6, "pluck"), t0 + s_ * step, 0.22)
            for m in ch:
                put(melodic, tone(m, bar, "pad"), t0, 0.07)
    # a little room reverb on the melodic part
    ir = rng.standard_normal(int(sr * 0.8)) * np.exp(-np.arange(int(sr * 0.8)) / (sr * 0.2))
    size = 1 << (n + len(ir)).bit_length()
    wet = np.fft.irfft(np.fft.rfft(melodic, size) * np.fft.rfft(ir, size), size)[:n]
    wet *= (np.abs(melodic).max() / (np.abs(wet).max() or 1)) * 0.5
    mix = melodic + wet + drums
    mix = np.tanh(mix * 1.1)
    mix /= (np.abs(mix).max() or 1) / 0.9
    fin, fout = int(sr * 0.25), int(sr * 1.6)
    mix[:fin] *= np.linspace(0, 1, fin)
    mix[-fout:] *= np.linspace(1, 0, fout)
    pcm = (mix * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())
    return path


# ---------------------------------------------------------------- picture scenes
def _reel_text(s):
    return re.sub(r"[^\w\s&'’.,\-+/()%₦:!@#]", "", s or "").strip()


def _glyph_ok(font, ch="₦"):
    try:
        return font.getmask(ch).getbbox() != font.getmask("￿").getbbox()
    except Exception:
        return False


def _price_text(font, s):
    return s if _glyph_ok(font) else s.replace("₦", "NGN ")


def _fit_cover(im, w, h):
    sc = max(w / im.width, h / im.height)
    im = im.resize((max(w, int(im.width * sc + .5)), max(h, int(im.height * sc + .5))))
    l, t = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((l, t, l + w, t + h))


def _fit_inside(im, w, h):
    sc = min(w / im.width, h / im.height)
    return im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))))


def _wrap(draw, text, font, maxw, lines=2):
    words, out, cur = text.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if draw.textlength(t, font=font) <= maxw or not cur:
            cur = t
        else:
            out.append(cur)
            cur = wd
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(".,") + "…"
    return out


def _reel_logo(size):
    from PIL import Image, ImageDraw
    lg = _logo_image()
    if lg is None:
        return None
    lg = _fit_cover(lg.convert("RGB"), size, size)
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(lg, (0, 0), m)
    return out


def _centered(draw, text, font, y, fill, W=REEL_W):
    draw.text(((W - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)


def _gradient():
    from PIL import Image
    top, bot = (24, 20, 14), (6, 6, 6)
    col = Image.new("RGB", (1, REEL_H))
    col.putdata([tuple(int(top[i] + (bot[i] - top[i]) * y / REEL_H) for i in range(3)) for y in range(REEL_H)])
    return col.resize((REEL_W, REEL_H))


def _fit_font(draw, text, size, maxw=880):
    while size > 30:
        f = _font(size)
        if draw.textlength(_price_text(f, text), font=f) <= maxw:
            return f
        size -= 4
    return _font(30)


def _pill(draw, text, font, cx, y, pad=36):
    text = _price_text(font, text)
    w = draw.textlength(text, font=font)
    h = font.size + 30
    draw.rounded_rectangle((cx - w / 2 - pad, y, cx + w / 2 + pad, y + h), radius=h // 2, fill=REEL_GOLD)
    draw.text((cx - w / 2, y + 12), text, font=font, fill=REEL_INK)
    return y + h


def _scene_photo(photo_path, name, price):
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.open(photo_path).convert("RGB")
    bg = _fit_cover(im, REEL_W, REEL_H).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (REEL_W, REEL_H), REEL_INK), 0.58)
    fg = _fit_inside(im, 960, 1260)
    msk = Image.new("L", fg.size, 0)
    ImageDraw.Draw(msk).rounded_rectangle((0, 0, fg.width - 1, fg.height - 1), radius=36, fill=255)
    x, y = (REEL_W - fg.width) // 2, 250 + (1260 - fg.height) // 2
    sh = Image.new("RGBA", (REEL_W, REEL_H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x, y + 18, x + fg.width, y + fg.height + 18), radius=36, fill=(0, 0, 0, 150))
    bg = Image.alpha_composite(bg.convert("RGBA"), sh.filter(ImageFilter.GaussianBlur(26))).convert("RGB")
    bg.paste(fg, (x, y), msk)
    d = ImageDraw.Draw(bg)
    lg = _reel_logo(104)
    if lg:
        bg.paste(lg, (56, 70), lg)
    d.text((184, 96), "AD FASHION SHOP", font=_font(44), fill=REEL_GOLD)
    fn, fp = _font(60), _fit_font(d, price, 62)
    yy = 1560
    for ln in _wrap(d, name, fn, 960):
        _centered(d, ln, fn, yy, (255, 255, 255))
        yy += 74
    if price:
        _pill(d, price, fp, REEL_W / 2, min(yy + 14, 1760))
    return bg


def _scene_intro():
    from PIL import ImageDraw
    bg = _gradient()
    d = ImageDraw.Draw(bg)
    lg = _reel_logo(520)
    if lg:
        d.ellipse((REEL_W / 2 - 280, 520, REEL_W / 2 + 280, 1080), outline=REEL_GOLD, width=8)
        bg.paste(lg, ((REEL_W - 520) // 2, 540), lg)
    _centered(d, "AD FASHION SHOP", _font(86), 1180, REEL_GOLD)
    _centered(d, "HIJABS  &  MORE", _font(46), 1310, (235, 235, 235))
    return bg


def _scene_outro(first_photo, name, price, site_txt, wa):
    from PIL import Image, ImageDraw
    bg = _gradient()
    d = ImageDraw.Draw(bg)
    try:
        im = Image.open(first_photo).convert("RGB")
        card = _fit_cover(im, 640, 640)
        msk = Image.new("L", (640, 640), 0)
        ImageDraw.Draw(msk).rounded_rectangle((0, 0, 639, 639), radius=40, fill=255)
        bg.paste(card, ((REEL_W - 640) // 2, 260), msk)
    except Exception:
        pass
    _centered(d, "ORDER NOW", _font(120), 1010, REEL_GOLD)
    fn, fp = _font(58), _fit_font(d, price, 60)
    yy = 1175
    for ln in _wrap(d, name, fn, 960):
        _centered(d, ln, fn, yy, (255, 255, 255))
        yy += 70
    if price:
        yy = _pill(d, price, fp, REEL_W / 2, yy + 10)
    f2 = _font(44, bold=False)
    _centered(d, "Tap the link in our bio", f2, yy + 50, (235, 235, 235))
    if wa:
        _centered(d, f"WhatsApp {wa}", f2, yy + 112, (235, 235, 235))
    _centered(d, "Bauchi, Nigeria  |  Delivery nationwide", _font(36, bold=False), 1790, (170, 170, 170))
    return bg


# ---------------------------------------------------------------- put it together with ffmpeg
def make_reel(p, take=0):
    """Build (or reuse) the Reel for a product. Returns the MP4 path."""
    import subprocess
    ff = _ffmpeg()
    if not ff:
        raise ReelError("The video tool (FFmpeg) is not installed on the server.")
    fids = share_photos(p["id"])[:6]
    paths = [x for x in (fetch_file(f) for f in fids) if x]
    if not paths:
        raise ReelError("This product has no picture to make a video from.")
    name = _reel_text(p["name"]) or "New arrival"
    price = display_price(p) if (p["price"] or fabric_prices(p["id"])) else ""
    price = re.sub(r"\s+", " ", price.replace("from ", "From "))
    wa = CFG.get("WHATSAPP", "")
    tone = tone_of(p)
    key = hashlib.sha256("|".join([p["id"], name, price, ",".join(fids), str(take), setting("web_logo"), wa,
                                    time.strftime("%Y-%m-%d")]).encode()).hexdigest()[:12]
    out = os.path.join(_reel_dir(), f"{re.sub(r'[^A-Za-z0-9]', '', p['id'])[:20] or 'p'}_{key}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        return out
    work = os.path.join(_reel_dir(), "tmp_" + key)
    os.makedirs(work, exist_ok=True)
    try:
        scenes = [(_scene_intro(), 1.6)]
        for pth in paths:
            scenes.append((_scene_photo(pth, name, price), 3.0 if len(paths) > 1 else 5.0))
        scenes.append((_scene_outro(paths[0], name, price, site_base(), wa), 2.8))
        files = []
        for i, (im, _d) in enumerate(scenes):
            f = os.path.join(work, f"s{i}.png")
            im.save(f)
            files.append(f)
        durs = [d for _i, d in scenes]
        total = sum(durs)
        seed = int(hashlib.sha256(f"{p['id']}-{take}-{time.strftime('%Y%m%d')}".encode()).hexdigest()[:8], 16)
        wav = os.path.join(work, "music.wav")
        make_music(_music_style(tone), total, seed, wav)

        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                why = (r.stderr or "").strip()[-250:]
                if r.returncode < 0 or not why:
                    why = f"the video tool was stopped (code {r.returncode}), most likely the server ran out of memory. " + why
                raise ReelError("Video maker error: " + why)

        def render(w, h, rate="2500k"):
            """One small clip per picture (one ffmpeg at a time keeps memory low), joined without re-encoding."""
            clips = []
            for i, f in enumerate(files):
                d = durs[i]
                frames = int(round(d * REEL_FPS))
                z = ("min(zoom+0.0006,1.10)" if i % 2 == 0 else "if(eq(on,0),1.10,max(zoom-0.0006,1.0))")
                vf = (f"scale={int(w * 1.1)}:{int(h * 1.1)},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                      f":d={frames}:s={w}x{h}:fps={REEL_FPS},fade=t=in:st=0:d=0.25,fade=t=out:st={d - 0.25:.2f}:d=0.25,format=yuv420p")
                clip = os.path.join(work, f"c{i}.mp4")
                run([ff, "-y", "-loglevel", "error", "-i", f, "-vf", vf, "-frames:v", str(frames), "-an",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", rate, "-bufsize", "2M",
                     "-profile:v", "high", "-pix_fmt", "yuv420p",
                     "-r", str(REEL_FPS), "-g", "60", "-threads", "1", "-filter_threads", "1",
                     "-x264-params", "rc-lookahead=8:sync-lookahead=0:ref=1:bframes=1", clip])
                clips.append(clip)
            lst = os.path.join(work, "list.txt")
            with open(lst, "w") as fh:
                fh.write("".join(f"file '{c}'\n" for c in clips))
            run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-i", wav,
                 "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                 "-t", f"{total:.2f}", "-movflags", "+faststart", out])

        try:
            render(REEL_W, REEL_H)
            if os.path.getsize(out) > REEL_MAX_BYTES:           # keep every video under 10 MB
                os.remove(out)
                render(720, 1280, "1200k")
        except ReelError as e:
            if "stopped" not in str(e):
                raise
            for f in os.listdir(work):                    # the server is small: try again at 720x1280 (still fine for Reels)
                if f.startswith("c") and f.endswith(".mp4"):
                    os.remove(os.path.join(work, f))
            render(720, 1280)
        if not os.path.exists(out) or os.path.getsize(out) < 10000:
            raise ReelError("Video maker error: the video file was not created.")
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    purge_reels(keep=out)
    return out


def purge_reels(older_minutes=60, keep=None):
    """Videos are only kept while needed (preview or sending). Anything older than an hour is deleted."""
    d, cutoff = _reel_dir(), time.time() - older_minutes * 60
    for x in os.listdir(d):
        pth = os.path.join(d, x)
        try:
            if pth != keep and ((x.endswith(".mp4") and os.path.getmtime(pth) < cutoff) or x.startswith("tmp_")):
                if os.path.isdir(pth):
                    import shutil
                    shutil.rmtree(pth, ignore_errors=True)
                else:
                    os.remove(pth)
        except OSError:
            pass


def _discard_video(path, delay=0):
    def rm():
        try:
            os.remove(path)
        except OSError:
            pass
    if delay:
        t = threading.Timer(delay, rm)
        t.daemon = True
        t.start()
    else:
        rm()


def _vid_sig(name):
    return _pub_sig("reel/" + name)


def pub_vid_url(path):
    n = os.path.basename(path)
    return f"{site_base()}/pubvid/{_vid_sig(n)}/{n}"


@bp.get("/pubvid/<sig>/<name>")
def pubvid(sig, name):
    """Public (signed) video link. Instagram and Facebook download the Reel from here."""
    import hmac
    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.mp4", name) or not hmac.compare_digest(sig, _vid_sig(name)):
        abort(404)
    path = os.path.join(_reel_dir(), name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="video/mp4", conditional=True, max_age=3600)


@bp.get("/admin/reelfile/<name>")
@admin_required
def admin_reelfile(name):
    path = os.path.join(_reel_dir(), name)
    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.mp4", name) or not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="video/mp4", conditional=True, max_age=300)


# ---------------------------------------------------------------- posting a Reel
def post_instagram_reel(video_url, caption):
    base, tok = _ig_target()
    ig = setting("meta_ig_id")
    cid = _graph("POST", f"/{ig}/media", {"media_type": "REELS", "video_url": video_url, "caption": caption,
                                           "share_to_feed": "true", "access_token": tok}, base=base)["id"]
    _ig_wait(cid, tok, base, tries=70, pause=3)                 # Instagram needs a little while to process a video
    return _graph("POST", f"/{ig}/media_publish", {"creation_id": cid, "access_token": tok}, base=base).get("id", "")


def post_facebook_reel(video_url, caption, info=None):
    page, tok = setting("meta_fb_page_id"), setting("meta_page_token")
    try:
        s = _graph("POST", f"/{page}/video_reels", {"upload_phase": "start", "access_token": tok})
        req = urllib.request.Request(s["upload_url"], data=b"", method="POST",
                                     headers={"Authorization": "OAuth " + tok, "file_url": video_url})
        urllib.request.urlopen(req, timeout=120).read()
        _graph("POST", f"/{page}/video_reels", {"upload_phase": "finish", "video_id": s["video_id"],
                                                 "video_state": "PUBLISHED", "description": caption, "access_token": tok})
        return s["video_id"]
    except Exception:
        # Fall back to an ordinary Facebook video if the Reel route is refused (Facebook then fetches the file later).
        if info is not None:
            info["late_fetch"] = True
        return _graph("POST", f"/{page}/videos", {"file_url": video_url, "description": caption, "access_token": tok}).get("id", "")


def do_reel(p, path, want, caps=None, auto=0):
    rdy, caps, out, pid, info = social_ready(), caps or {}, [], p["id"], {}
    url = pub_vid_url(path)
    try:
        return _do_reel(p, url, want, rdy, caps, out, pid, info, auto)
    finally:                                      # the video is not kept on the server once it has been sent
        _discard_video(path, delay=900 if info.get("late_fetch") else 0)


def _do_reel(p, url, want, rdy, caps, out, pid, info, auto=0):
    for plat, fn in (("fb", post_facebook_reel), ("ig", post_instagram_reel)):
        if plat not in want or not rdy[plat]:
            continue
        label = "Facebook" if plat == "fb" else "Instagram"
        caption = (caps.get(plat) or "").strip()
        if not caption or _norm(caption) in used_captions(pid, label):
            caption = fresh_caption(p, plat)
        try:
            rid = fn(url, caption[:2100], info) if plat == "fb" else fn(url, caption[:2100])
            db().execute("INSERT INTO social_posts(product_id,platform,status,remote_id,caption,auto) VALUES(?,?,?,?,?,?)",
                         (pid, label, "ok", str(rid), caption[:2100], auto))
            out.append(f"✅ Posted Reel to {label}")
        except Exception as e:
            db().execute("INSERT INTO social_posts(product_id,platform,status,error,auto) VALUES(?,?,?,?,?)",
                         (pid, label, "failed", ("Reel: " + str(e))[:300], auto))
            out.append(f"❌ {label} Reel failed: {e}")
        db().commit()
    return out


@bp.route("/admin/share/reel/<pid>", methods=["GET", "POST"])
@admin_required
def admin_share_reel(pid):
    p = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    take = to_int(request.values.get("n"), 0) or 0
    try:
        path = make_reel(p, take)
    except ReelError as e:
        flash(f"Could not make the video: {e}")
        return redirect(url_for("web.admin_share"))
    if request.method == "POST":
        rdy = social_ready()
        want = [pl for pl in ("fb", "ig") if setting("auto_" + pl, "1") == "1" and rdy[pl]]
        if not want:
            flash("Connect Facebook or Instagram first.")
        elif queue_job("reel", pid, want, take=take) is None:
            flash("This Reel is already being sent. Please wait a moment.")
        else:
            flash("📤 Sending the Reel in the background. You can leave the app. We will notify you when it is done.")
        return redirect(url_for("web.admin_share"))
    name = os.path.basename(path)
    return admin_page("Reel preview", """<p><a href="{{ url_for('web.admin_share') }}">‹ Share</a></p>
<h2>🎬 Reel: {{ p['name'] }}</h2>
<video src="{{ url_for('web.admin_reelfile', name=name) }}" controls playsinline preload="metadata" style="width:100%;max-width:340px;border-radius:14px;background:#000"></video>
<p class="mut small">This is exactly what will be posted: photos, text and the music ({{ style }} style).</p>
<form method="post" class="row" style="margin-top:10px"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="n" value="{{ take }}">
<button class="btn gold">📣 Post this Reel now</button>
<a class="btn alt" href="{{ url_for('web.admin_share_reel', pid=p['id'], n=take+1) }}">🔄 Another take (new music)</a></form>""",
                      p=p, name=name, take=take, style=_music_style(tone_of(p)))
