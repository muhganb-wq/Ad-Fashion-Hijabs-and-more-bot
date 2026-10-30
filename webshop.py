"""
AD Fashion Hijabs & More - companion web shop.

Runs inside the SAME process / database as the Telegram bot, so the catalog,
photos, orders and statuses are always identical in both places.
Hooked up from bot.py with:   webshop.register(app_flask, cfg)
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
from functools import wraps

from flask import (
    Blueprint, abort, flash, g, redirect, render_template_string,
    request, send_file, session, url_for, Response,
)
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("web", __name__)
CFG = {}

def cats():
    """Live category labels from the bot (emoji stripped for the web)."""
    out = {}
    for k, v in (CFG.get("CATEGORY_LABELS") or {}).items():
        out[k] = re.sub(r"^[^\w]+", "", v).strip() or k
    return out


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
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_login_attempts = {}


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
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            score INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS web_wishlist(
            user_id INTEGER NOT NULL, product_id TEXT NOT NULL,
            PRIMARY KEY(user_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS news_posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            kind TEXT DEFAULT 'news',
            pinned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
    if "web_user_id" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN web_user_id INTEGER")
    c.commit()
    c.close()


@bp.teardown_app_request
def close_db(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


# ------------------------------------------------------------------ helpers
def money(v):
    return "Price on request" if v is None else f"₦{v:,.0f}"


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    if "user" not in g:
        g.user = db().execute("SELECT * FROM web_users WHERE id=?", (uid,)).fetchone()
    return g.user


def admin_emails():
    """ADMIN_EMAIL may hold one or several emails (comma/space separated); quotes are ignored."""
    raw = (os.environ.get("ADMIN_EMAIL") or "").strip().strip("\"'")
    return {e.strip().strip("\"'").lower() for e in re.split(r"[,\s;]+", raw) if e.strip()}


def is_admin_user():
    u = current_user()
    return bool(u and u["email"].strip().lower() in admin_emails())


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user():
            flash("Please log in to continue.")
            return redirect(url_for("web.login", next=request.path))
        return f(*a, **k)
    return w


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@bp.before_request
def csrf_protect():
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
            abort(400, "Session expired. Go back and try again.")


def products(category=None, q=None, sub=None):
    sql, args = "SELECT * FROM products WHERE active=1", []
    if sub:
        sql += " AND subcategory=?"; args.append(sub)
    if category:
        sql += " AND category=?"; args.append(category)
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    return db().execute(sql + " ORDER BY name", args).fetchall()


def product_photos(pid):
    rows = db().execute(
        "SELECT file_id,label FROM product_photos WHERE product_id=? ORDER BY position,id", (pid,)).fetchall()
    out = [{"file_id": r["file_id"], "label": r["label"] or ""} for r in rows]
    if not out:
        r = db().execute("SELECT photo_file_id FROM products WHERE id=?", (pid,)).fetchone()
        if r and r["photo_file_id"]:
            out = [{"file_id": r["photo_file_id"], "label": ""}]
    return out


def cover(pid_row):
    return pid_row["photo_file_id"] or ""


def setting(key, default=""):
    r = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r and r["value"] is not None else default


def discount_pct():
    try:
        return max(0, min(90, int(setting("discount_percent", "0") or 0)))
    except ValueError:
        return 0


def delivery_fee(method):
    try:
        return max(0, int(setting(f"delivery_fee_{method}", "0") or 0))
    except ValueError:
        return 0


def pricing(items_total, method="pickup"):
    off = int(items_total * discount_pct() / 100) if items_total else 0
    fee = delivery_fee(method)
    return off, fee, items_total - off + fee


STEPS = ["AWAITING_PAYMENT", "PAID", "PROCESSING", "OUT_FOR_DELIVERY", "DELIVERED"]


def cart():
    return session.setdefault("cart", {})


def cart_detail():
    items, total = [], 0
    for key, qty in list(cart().items()):
        pid, _, cid = key.partition(":")
        p = db().execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            cart().pop(key, None); continue
        color = None
        if cid:
            color = db().execute("SELECT * FROM color_options WHERE id=?", (cid,)).fetchone()
        line = (p["price"] or 0) * qty
        total += line
        items.append({"key": key, "p": p, "qty": qty, "line": line, "color": color})
    return items, total


def tg_notify(text):
    """Tell the admin on Telegram (best effort, never breaks the web request)."""
    try:
        data = urllib.parse.urlencode({"chat_id": CFG["ADMIN_ID"], "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{CFG['BOT_TOKEN']}/sendMessage", data=data, timeout=8)
    except Exception:
        pass


def tg_send_photo(file_bytes, caption):
    try:
        boundary = secrets.token_hex(8)
        parts = []
        for k, v in (("chat_id", str(CFG["ADMIN_ID"])), ("caption", caption)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="receipt.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode())
        parts.append(file_bytes); parts.append(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{CFG['BOT_TOKEN']}/sendPhoto", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


# ------------------------------------------------------------------ images (Telegram file_id proxy)
def _known_file_id(fid):
    d = db()
    for sql in ("SELECT 1 FROM products WHERE photo_file_id=?",
                "SELECT 1 FROM product_photos WHERE file_id=?",
                "SELECT 1 FROM color_options WHERE photo_file_id=?"):
        if d.execute(sql, (fid,)).fetchone():
            return True
    return False


@bp.get("/img/<path:fid>")
def img(fid):
    if not _known_file_id(fid):
        abort(404)
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(CFG["DB"])), "web_cache")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, hashlib.sha256(fid.encode()).hexdigest() + ".img")
    if not os.path.exists(path):
        try:
            base = f"https://api.telegram.org/bot{CFG['BOT_TOKEN']}"
            info = json.load(urllib.request.urlopen(f"{base}/getFile?file_id={urllib.parse.quote(fid)}", timeout=10))
            fp = info["result"]["file_path"]
            blob = urllib.request.urlopen(f"https://api.telegram.org/file/bot{CFG['BOT_TOKEN']}/{fp}", timeout=20).read()
            with open(path, "wb") as f:
                f.write(blob)
        except Exception:
            abort(404)
    r = send_file(path, mimetype="image/jpeg", max_age=86400 * 30)
    return r


# ------------------------------------------------------------------ templates
BASE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title or cfg.NAME }}</title>
<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#5b2a54">
<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#fbf7f1;--ink:#2a1f2d;--mut:#7a6c7e;--card:#fff;--line:#eadfd2;--accent:#5b2a54;--gold:#b8893b;--ok:#1d7a4d}
@media (prefers-color-scheme:dark){:root{--bg:#171218;--ink:#f3ebe4;--mut:#a899ab;--card:#221a24;--line:#3a2e3d;--accent:#d8a6cf;--gold:#e0b45f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 Inter,system-ui,sans-serif}
a{color:inherit}h1,h2,h3{font-family:'Cormorant Garamond',serif;font-weight:700;line-height:1.15;margin:.2em 0}
header{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--line)}
.bar{max-width:1060px;margin:auto;padding:12px 16px;display:flex;align-items:center;gap:14px}
.logo{font:700 21px 'Cormorant Garamond',serif;text-decoration:none;color:var(--accent);flex:1}
.bar a.n{text-decoration:none;font-size:14px;color:var(--mut)}.bar a.n:hover{color:var(--ink)}
main{max-width:1060px;margin:auto;padding:18px 16px 70px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;text-decoration:none;display:block}
.card img{width:100%;aspect-ratio:4/5;object-fit:cover;display:block;background:var(--line)}
.card .b{padding:10px 12px}.card .t{font-weight:600;font-size:15px}.price{color:var(--gold);font-weight:600}
.btn{display:inline-block;border:0;background:var(--accent);color:#fff;padding:12px 20px;border-radius:999px;font:600 15px Inter;cursor:pointer;text-decoration:none}
.btn.alt{background:transparent;color:var(--accent);border:1.5px solid var(--accent)}.btn.wa{background:#1f9d55}
@media (prefers-color-scheme:dark){.btn{color:#171218}}
.pills{display:flex;gap:8px;overflow:auto;padding:4px 0 12px}.pill{white-space:nowrap;padding:8px 16px;border:1px solid var(--line);border-radius:999px;text-decoration:none;font-size:14px;background:var(--card)}
.pill.on{background:var(--accent);color:#fff}
.gal{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;gap:8px;border-radius:14px}
.gal figure{margin:0;flex:0 0 100%;scroll-snap-align:center;position:relative}
.gal img{width:100%;aspect-ratio:4/5;object-fit:cover;border-radius:14px;display:block}
.gal figcaption{position:absolute;left:10px;bottom:10px;background:rgba(0,0,0,.6);color:#fff;padding:4px 12px;border-radius:999px;font-size:13px}
.thumbs{display:flex;gap:8px;margin:10px 0}.thumbs img{width:56px;height:56px;object-fit:cover;border-radius:10px;border:2px solid transparent;cursor:pointer}
.two{display:grid;gap:22px;grid-template-columns:1fr}@media(min-width:760px){.two{grid-template-columns:1fr 1fr}}
input,select,textarea{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font:inherit}
label{display:block;margin:12px 0 4px;font-size:14px;color:var(--mut)}
.flash{background:var(--card);border:1px solid var(--gold);padding:10px 14px;border-radius:10px;margin-bottom:12px}
.hero{background:linear-gradient(135deg,var(--accent),#2a1330);color:#fff;border-radius:18px;padding:28px 22px;margin-bottom:18px}
.hero h1{font-size:34px;color:#fff}.hero p{opacity:.9;margin:.3em 0 1em}
.swatches{display:flex;gap:10px;flex-wrap:wrap}.sw input{display:none}.sw img{width:64px;height:64px;object-fit:cover;border-radius:12px;border:3px solid transparent}
.sw input:checked+img{border-color:var(--accent)}
table{width:100%;border-collapse:collapse}td,th{padding:10px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
.tag{display:inline-block;padding:3px 10px;border-radius:999px;background:var(--line);font-size:12px}.tag.g{background:var(--gold);color:#fff}
.mut{color:var(--mut)}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.heart{background:none;border:0;font-size:22px;cursor:pointer;color:var(--accent)}
footer{text-align:center;color:var(--mut);font-size:13px;padding:30px 16px}
</style></head><body>
<header><div class="bar">
<a class="logo" href="{{ url_for('web.home') }}">{{ cfg.NAME.title() }}</a>
<a class="n" href="{{ url_for('web.shop') }}">Shop</a>
<a class="n" href="{{ url_for('web.news') }}">News</a>
<a class="n" href="{{ url_for('web.cart_view') }}">Cart{% if cart_count %} ({{ cart_count }}){% endif %}</a>
{% if is_admin %}<a class="n" href="{{ url_for('web.news_new') }}" style="color:var(--gold);font-weight:600">Post news</a>{% endif %}
{% if user %}<a class="n" href="{{ url_for('web.account') }}">{{ user['name'].split()[0] }}</a>
{% else %}<a class="n" href="{{ url_for('web.login') }}">Log in</a>{% endif %}
</div></header>
<main>
{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}
{{ body|safe }}
</main>
<div id="inst" style="display:none;position:fixed;left:12px;right:12px;bottom:12px;background:var(--accent);color:#fff;padding:12px 16px;border-radius:14px;z-index:20;align-items:center;gap:12px">
<span style="flex:1;font-size:14px">Install the {{ cfg.NAME.title() }} app on your phone</span>
<button id="instbtn" style="border:0;border-radius:99px;padding:8px 16px;font-weight:600">Install</button>
<button id="instx" style="border:0;background:none;color:#fff;font-size:18px">✕</button></div>
<script>
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){});}
var dp=null;window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();dp=e;var b=document.getElementById('inst');if(b&&!sessionStorage.getItem('instx')){b.style.display='flex';}});
document.getElementById('instbtn').onclick=function(){if(dp){dp.prompt();dp=null;}document.getElementById('inst').style.display='none';};
document.getElementById('instx').onclick=function(){document.getElementById('inst').style.display='none';try{sessionStorage.setItem('instx','1')}catch(e){}};
</script>
<footer>{{ cfg.NAME }} · {{ cfg.ADDRESS }}<br>Call {{ cfg.PHONE1 }} · <a href="https://wa.me/{{ cfg.WHATSAPP }}">WhatsApp</a>
<br>Same shop as our Telegram bot — your orders appear in both.</footer>
</body></html>"""


def page(title, body_tpl, **ctx):
    body = render_template_string(body_tpl, csrf=csrf_token(), money=money, cfg=CFG,
                                  user=current_user(), status_label=STATUS_LABEL, **ctx)
    return render_template_string(
        BASE, title=title, body=body, cfg=CFG, user=current_user(), is_admin=is_admin_user(),
        cart_count=sum(cart().values()))


# ------------------------------------------------------------------ pages
@bp.get("/")
def home():
    latest = db().execute("SELECT * FROM news_posts ORDER BY pinned DESC, id DESC LIMIT 2").fetchall()
    new = db().execute("SELECT * FROM products WHERE active=1 ORDER BY rowid DESC LIMIT 8").fetchall()
    return page("Home", """
{% if pct %}<div class="flash" style="text-align:center;font-weight:600">🎉 {{ pct }}% OFF everything — applied automatically at checkout!</div>{% endif %}
<div class="hero"><h1>{{ cfg.TAGLINE.title() }}</h1>
<p>Hijabs, jilbabs, textiles and more — shop here or on Telegram, it's one shop.</p>
<a class="btn" style="background:#fff;color:#5b2a54" href="{{ url_for('web.shop') }}">Shop now</a></div>
<div class="pills">{% for k,v in cats.items() %}<a class="pill" href="{{ url_for('web.shop', c=k) }}">{{ v }}</a>{% endfor %}</div>
{% if latest %}<h2>Latest updates</h2>{% for n in latest %}
<div class="card" style="padding:14px;margin-bottom:10px"><span class="tag {{ 'g' if n['kind']=='giveaway' }}">{{ n['kind']|title }}</span>
<h3>{{ n['title'] }}</h3><p class="mut">{{ n['body'][:160] }}{% if n['body']|length>160 %}…{% endif %}</p></div>{% endfor %}
<a href="{{ url_for('web.news') }}">All updates →</a>{% endif %}
<h2 style="margin-top:22px">New arrivals</h2>
<div class="grid">{% for p in new %}{% include_card %}{% endfor %}</div>
""".replace("{% include_card %}", CARD), cats=cats(), latest=latest, new=new, pct=discount_pct())


CARD = """<a class="card" href="{{ url_for('web.product', pid=p['id']) }}">
{% if p['photo_file_id'] %}<img loading="lazy" src="{{ url_for('web.img', fid=p['photo_file_id']) }}" alt="{{ p['name'] }}">{% else %}<img alt="">{% endif %}
<div class="b"><div class="t">{{ p['name'] }}</div><div class="price">{{ money(p['price']) }}</div></div></a>"""


@bp.get("/shop")
def shop():
    c, q = request.args.get("c"), (request.args.get("q") or "").strip()
    subs = CFG.get("SUBCATEGORIES", {}).get(c, []) if c in cats() else []
    sub = request.args.get("s") if request.args.get("s") in [k for k, _ in subs] else None
    items = products(c if c in cats() else None, q or None, sub)
    return page("Shop", """
<form method="get" class="row"><input name="q" value="{{ q }}" placeholder="Search hijabs, abayas, fabrics…" style="flex:1"><button class="btn">Search</button></form>
<div class="pills"><a class="pill {{ 'on' if not c }}" href="{{ url_for('web.shop') }}">All</a>
{% for k,v in cats.items() %}<a class="pill {{ 'on' if c==k }}" href="{{ url_for('web.shop', c=k) }}">{{ v }}</a>{% endfor %}</div>
{% if subs %}<div class="pills">{% for k,lab in subs %}<a class="pill {{ 'on' if sub==k }}" href="{{ url_for('web.shop', c=c, s=k) }}">{{ lab }}</a>{% endfor %}</div>{% endif %}
{% if not items %}<p class="mut">Nothing found.</p>{% endif %}
<div class="grid">{% for p in items %}""" + CARD + """{% endfor %}</div>""",
        items=items, cats=cats(), c=c, q=q, subs=subs, sub=sub)


@bp.get("/p/<pid>")
def product(pid):
    p = db().execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not p:
        abort(404)
    photos = product_photos(pid)
    colors = []
    if (p["category"] or "").lower() in CFG.get("COLOR_CATEGORIES", ()):
        colors = db().execute("SELECT * FROM color_options WHERE active=1 ORDER BY id").fetchall()
    wished = False
    u = current_user()
    if u:
        wished = bool(db().execute("SELECT 1 FROM web_wishlist WHERE user_id=? AND product_id=?", (u["id"], pid)).fetchone())
    share = f"https://wa.me/?text={urllib.parse.quote(p['name'] + ' — ' + request.url)}"
    return page(p["name"], """
<div class="two"><div>
<div class="gal" id="gal">{% for ph in photos %}<figure id="ph{{ loop.index0 }}"><img src="{{ url_for('web.img', fid=ph.file_id) }}" alt="">
{% if ph.label %}<figcaption>{{ ph.label }}</figcaption>{% endif %}</figure>{% else %}<figure><img alt=""></figure>{% endfor %}</div>
{% if photos|length>1 %}<div class="thumbs">{% for ph in photos %}<a href="#ph{{ loop.index0 }}"><img src="{{ url_for('web.img', fid=ph.file_id) }}" alt=""></a>{% endfor %}</div>
<p class="mut" style="font-size:13px">Swipe the photos to see every colour →</p>{% endif %}
</div><div>
<h1>{{ p['name'] }}</h1><div class="price" style="font-size:22px">{{ money(p['price']) }}</div>
<p>{{ p['description'] or '' }}</p>
{% if p['variety'] %}<p><b>Variety:</b> {{ p['variety'] }}</p>{% endif %}
{% if p['features'] %}<p><b>Features:</b> {{ p['features'] }}</p>{% endif %}
<form method="post" action="{{ url_for('web.cart_add') }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="pid" value="{{ p['id'] }}">
{% if colors %}<label>Choose colour / fabric</label><div class="swatches">
{% for co in colors %}<label class="sw"><input type="radio" name="color" value="{{ co['id'] }}" {{ 'checked' if loop.first }}>
<img src="{{ url_for('web.img', fid=co['photo_file_id']) }}" alt="Option {{ co['id'] }}"></label>{% endfor %}</div>{% endif %}
<label>Quantity</label><input type="number" name="qty" value="1" min="1" max="500" style="max-width:120px">
<div class="row" style="margin-top:16px"><button class="btn">Add to cart</button>
<a class="btn wa" href="{{ share }}" target="_blank" rel="noopener">Share on WhatsApp</a></div></form>
{% if user %}<form method="post" action="{{ url_for('web.wish', pid=p['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="heart" title="Wishlist">{{ '♥ Saved' if wished else '♡ Save for later' }}</button></form>{% endif %}
</div></div>""", p=p, photos=photos, colors=colors, wished=wished, share=share)


@bp.post("/wish/<pid>")
@login_required
def wish(pid):
    u = current_user()
    if db().execute("SELECT 1 FROM web_wishlist WHERE user_id=? AND product_id=?", (u["id"], pid)).fetchone():
        db().execute("DELETE FROM web_wishlist WHERE user_id=? AND product_id=?", (u["id"], pid))
    else:
        db().execute("INSERT INTO web_wishlist VALUES(?,?)", (u["id"], pid))
    db().commit()
    return redirect(url_for("web.product", pid=pid))


@bp.get("/wishlist")
@login_required
def wishlist():
    rows = db().execute("""SELECT p.* FROM products p JOIN web_wishlist w ON w.product_id=p.id
                           WHERE w.user_id=? AND p.active=1""", (current_user()["id"],)).fetchall()
    return page("Wishlist", "<h2>My wishlist</h2><div class=grid>{% for p in items %}" + CARD + "{% endfor %}</div>"
                "{% if not items %}<p class=mut>Nothing saved yet.</p>{% endif %}", items=rows)


# ---- cart
@bp.post("/cart/add")
def cart_add():
    pid = request.form.get("pid", "")
    p = db().execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
    if not p:
        abort(404)
    try:
        qty = max(1, min(500, int(request.form.get("qty", 1))))
    except ValueError:
        qty = 1
    color = request.form.get("color", "")
    key = f"{pid}:{int(color)}" if color.isdigit() else pid
    cart()[key] = cart().get(key, 0) + qty
    session.modified = True
    flash(f"Added {p['name']} to your cart.")
    return redirect(url_for("web.cart_view"))


@bp.post("/cart/update")
def cart_update():
    key = request.form.get("key", "")
    try:
        qty = int(request.form.get("qty", 1))
    except ValueError:
        qty = 1
    if qty <= 0:
        cart().pop(key, None)
    elif key in cart():
        cart()[key] = min(qty, 500)
    session.modified = True
    return redirect(url_for("web.cart_view"))


@bp.get("/cart")
def cart_view():
    items, total = cart_detail()
    return page("Cart", """
<h2>Your cart</h2>{% if not items %}<p class="mut">Your cart is empty. <a href="{{ url_for('web.shop') }}">Start shopping</a></p>{% else %}
<table>{% for i in items %}<tr><td><b>{{ i.p['name'] }}</b>{% if i.color %}<br><img src="{{ url_for('web.img', fid=i.color['photo_file_id']) }}" style="width:36px;height:36px;border-radius:8px;object-fit:cover"> <span class="mut">Colour #{{ i.color['id'] }}</span>{% endif %}</td>
<td><form method="post" action="{{ url_for('web.cart_update') }}" class="row"><input type="hidden" name="csrf" value="{{ csrf }}">
<input type="hidden" name="key" value="{{ i.key }}"><input type="number" name="qty" value="{{ i.qty }}" min="0" style="width:74px"><button class="btn alt" style="padding:8px 14px">Update</button></form></td>
<td>{{ money(i.line) }}</td></tr>{% endfor %}</table>
{% if off %}<p style="text-align:right" class="mut">Subtotal {{ money(total) }}<br><b style="color:var(--ok)">{{ pct }}% store discount: -{{ money(off) }}</b></p>{% endif %}
<h3 style="text-align:right">Total: {{ money(total-off) }}</h3>
<p class="mut" style="text-align:right">Delivery fee (if any) is added at checkout.</p>
<p style="text-align:right"><a class="btn" href="{{ url_for('web.checkout') }}">Checkout</a></p>{% endif %}""",
        items=items, total=total, off=pricing(total)[0], pct=discount_pct())


# ---- checkout
@bp.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    items, total = cart_detail()
    if not items:
        return redirect(url_for("web.cart_view"))
    u = current_user()
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
            extra = f" [Colour #{i['color']['id']}]" if i["color"] else ""
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
             "AWAITING_PAYMENT", method, city, state, notes,
             ",".join(i["p"]["id"] for i in items), u["id"]))
        oid = cur.lastrowid
        for i in items:
            d.execute("""INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price,color_id,color_photo_file_id)
                         VALUES(?,?,?,?,?,?,?)""",
                      (oid, i["p"]["id"], i["p"]["name"], i["qty"], i["p"]["price"] or 0,
                       i["color"]["id"] if i["color"] else None,
                       i["color"]["photo_file_id"] if i["color"] else ""))
        d.execute("UPDATE web_users SET score=score+5, phone=? WHERE id=?", (phone, u["id"]))
        d.commit()
        session["cart"] = {}
        tg_notify(f"🌐 New WEB order #AD-{oid:05d}\n{u['name']} ({u['email']})\n{phone}\n"
                  f"{DELIVERY[method]}\n\n" + "\n".join(lines) + f"\n\nTotal: {money(grand)}")
        return redirect(url_for("web.order", oid=oid))
    return page("Checkout", """
<h2>Checkout</h2><p class="mut">Items: <b>{{ money(total-off) }}</b>{% if off %} (after {{ pct }}% discount){% endif %} · {{ items|length }} item(s)</p>
<p class="mut" style="font-size:13px">Delivery fees: {% for k,v in dm.items() %}{{ v }} — {{ money(fees[k]) if fees[k] else 'free / confirmed by us' }}{{ '; ' if not loop.last }}{% endfor %}</p>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>Delivery method</label><select name="method">{% for k,v in dm.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select>
<label>Phone number</label><input name="phone" value="{{ user['phone'] }}" required>
<label>Address (for delivery)</label><textarea name="address" rows="2"></textarea>
<div class="row"><div style="flex:1"><label>City</label><input name="city"></div><div style="flex:1"><label>State</label><input name="state"></div></div>
<label>Notes (optional)</label><input name="notes">
<p><button class="btn">Place order</button></p></form>""", total=total, items=items, dm=DELIVERY, off=pricing(total)[0], pct=discount_pct(), fees={k: delivery_fee(k) for k in DELIVERY})


@bp.get("/order/<int:oid>")
@login_required
def order(oid):
    o = db().execute("SELECT * FROM orders WHERE id=? AND web_user_id=?", (oid, current_user()["id"])).fetchone()
    if not o:
        abort(404)
    paystack = bool(os.environ.get("PAYSTACK_SECRET"))
    return page(f"Order #AD-{oid:05d}", """
<h2>Order #AD-{{ '%05d'|format(o['id']) }}</h2>
<p><span class="tag g">{{ status_label.get(o['status'], o['status']) }}</span></p>
{% if o['status'] in steps %}{% set cur = steps.index(o['status']) %}
<div style="display:flex;gap:6px;margin:14px 0">{% for st in steps %}<div style="flex:1;text-align:center;font-size:11px;color:{{ 'var(--ink)' if loop.index0<=cur else 'var(--mut)' }}">
<div style="height:8px;border-radius:99px;background:{{ 'var(--ok)' if loop.index0<=cur else 'var(--line)' }};margin-bottom:6px"></div>{{ status_label[st] }}</div>{% endfor %}</div>
{% elif o['status']=='CANCELLED' or o['status']=='DELIVERY_FAILED' %}<p class="mut">Please contact us on WhatsApp about this order.</p>{% endif %}
<pre style="white-space:pre-wrap;font:inherit">{{ o['items'] }}</pre>
<h3>Total {{ money(o['total']) }}</h3>
{% if o['status'] in ('AWAITING_PAYMENT','PENDING') %}
<div class="card" style="padding:16px;margin:14px 0"><b>Pay by bank transfer</b>
{% for bank,name,num in cfg.PAYMENT_ACCOUNTS %}<p style="margin:.5em 0">{{ bank }} — <b>{{ num }}</b><br><span class="mut">{{ name }}</span></p>{% endfor %}
<p class="mut">Then upload your receipt so we can confirm quickly. Use order number AD-{{ '%05d'|format(o['id']) }} as the narration.</p>
<form method="post" action="{{ url_for('web.receipt', oid=o['id']) }}" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{{ csrf }}">
<input type="file" name="receipt" accept="image/*" required><p><button class="btn">Send receipt</button></p></form></div>
{% if paystack %}<form method="post" action="{{ url_for('web.paystack_start', oid=o['id']) }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<button class="btn alt">Pay online with card / USSD</button></form>{% endif %}{% endif %}
<p><a class="btn wa" href="https://wa.me/{{ cfg.WHATSAPP }}?text=Hello, about order AD-{{ '%05d'|format(o['id']) }}">Chat on WhatsApp</a></p>""",
        o=o, paystack=paystack, steps=STEPS)


@bp.post("/order/<int:oid>/receipt")
@login_required
def receipt(oid):
    o = db().execute("SELECT * FROM orders WHERE id=? AND web_user_id=?", (oid, current_user()["id"])).fetchone()
    f = request.files.get("receipt")
    if not o or not f:
        abort(400)
    blob = f.read(6_000_000)
    if not blob[:4] in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xdb", b"\x89PNG") and blob[:3] != b"\xff\xd8\xff":
        flash("Please upload a JPG or PNG image."); return redirect(url_for("web.order", oid=oid))
    db().execute("UPDATE orders SET payment_notified=1, payment_method='WEB_RECEIPT' WHERE id=?", (oid,))
    db().commit()
    tg_send_photo(blob, f"🧾 Receipt for WEB order #AD-{oid:05d} ({o['customer_name']}) — {money(o['total'])}")
    flash("Receipt received. We'll confirm your payment shortly.")
    return redirect(url_for("web.order", oid=oid))


# ---- optional Paystack (set PAYSTACK_SECRET + SITE_URL env vars)
@bp.post("/order/<int:oid>/paystack")
@login_required
def paystack_start(oid):
    key = os.environ.get("PAYSTACK_SECRET")
    o = db().execute("SELECT * FROM orders WHERE id=? AND web_user_id=?", (oid, current_user()["id"])).fetchone()
    if not key or not o:
        abort(404)
    site = (os.environ.get("SITE_URL") or request.host_url).rstrip("/")
    body = json.dumps({"email": current_user()["email"], "amount": int(o["total"]) * 100,
                       "reference": f"AD{oid}-{secrets.token_hex(4)}",
                       "callback_url": f"{site}{url_for('web.paystack_cb', oid=oid)}"}).encode()
    req = urllib.request.Request("https://api.paystack.co/transaction/initialize", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=15))
        return redirect(res["data"]["authorization_url"])
    except Exception:
        flash("Online payment is unavailable right now. Please use bank transfer.")
        return redirect(url_for("web.order", oid=oid))


@bp.get("/order/<int:oid>/paystack/callback")
@login_required
def paystack_cb(oid):
    key, ref = os.environ.get("PAYSTACK_SECRET"), request.args.get("reference", "")
    o = db().execute("SELECT * FROM orders WHERE id=? AND web_user_id=?", (oid, current_user()["id"])).fetchone()
    if not (key and o and ref.startswith(f"AD{oid}-")):
        abort(400)
    try:
        req = urllib.request.Request(f"https://api.paystack.co/transaction/verify/{urllib.parse.quote(ref)}",
                                     headers={"Authorization": f"Bearer {key}"})
        d = json.load(urllib.request.urlopen(req, timeout=15))["data"]
        if d.get("status") == "success" and int(d.get("amount", 0)) >= int(o["total"]) * 100:
            db().execute("UPDATE orders SET status='PAID', payment_method='PAYSTACK' WHERE id=?", (oid,))
            db().commit()
            tg_notify(f"💳 Paystack payment confirmed for WEB order #AD-{oid:05d} ({money(o['total'])})")
            flash("Payment confirmed. Thank you!")
        else:
            flash("Payment not completed.")
    except Exception:
        flash("Could not verify payment yet. If you were charged, contact us on WhatsApp.")
    return redirect(url_for("web.order", oid=oid))


@bp.get("/orders")
@login_required
def orders():
    rows = db().execute("SELECT * FROM orders WHERE web_user_id=? ORDER BY id DESC", (current_user()["id"],)).fetchall()
    return page("My orders", """<h2>My orders</h2>{% if not rows %}<p class=mut>No orders yet.</p>{% endif %}
<table>{% for o in rows %}<tr><td><a href="{{ url_for('web.order', oid=o['id']) }}">#AD-{{ '%05d'|format(o['id']) }}</a></td>
<td>{{ money(o['total']) }}</td><td><span class="tag">{{ status_label.get(o['status'], o['status']) }}</span></td></tr>{% endfor %}</table>""", rows=rows)


# ---- accounts
@bp.route("/register", methods=["GET", "POST"])
def register_page():
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
            session.clear(); session["uid"] = cur.lastrowid
            flash("Welcome! You've earned 10 style points.")
            return redirect(url_for("web.shop"))
    return page("Create account", """<h2>Create your account</h2><p class="mut">No email verification needed.</p>
<form method="post" style="max-width:420px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>Full name</label><input name="name" required><label>Email</label><input type="email" name="email" required>
<label>Password</label><input type="password" name="password" minlength="6" required>
<p><button class="btn">Sign up</button> <a href="{{ url_for('web.login') }}">I have an account</a></p></form>""")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]
        hits = [t for t in _login_attempts.get(ip, []) if time.time() - t < 600]
        if len(hits) >= 10:
            flash("Too many attempts. Try again in a few minutes.")
        else:
            u = db().execute("SELECT * FROM web_users WHERE email=?", (email,)).fetchone()
            if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
                keep = session.get("cart", {})
                session.clear(); session["uid"] = u["id"]; session["cart"] = keep
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("web.shop"))
            _login_attempts[ip] = hits + [time.time()]
            flash("Wrong email or password.")
    return page("Log in", """<h2>Log in</h2><form method="post" style="max-width:420px"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>Email</label><input type="email" name="email" required><label>Password</label><input type="password" name="password" required>
<p><button class="btn">Log in</button> <a href="{{ url_for('web.register_page') }}">Create account</a></p></form>""")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.home"))


@bp.get("/account")
@login_required
def account():
    return page("Account", """<h2>Hello, {{ user['name'] }}</h2>
<div class="card" style="padding:16px;max-width:420px"><span class="mut">Style points</span><h1 style="color:var(--gold)">{{ user['score'] }}</h1>
<p class="mut">Earn 10 for joining and 5 for every order. Ask us how to use them!</p></div>
<p class="row"><a class="btn alt" href="{{ url_for('web.orders') }}">My orders</a><a class="btn alt" href="{{ url_for('web.wishlist') }}">Wishlist</a>
{% if is_admin %}<a class="btn alt" href="{{ url_for('web.news_new') }}">Post news</a>{% endif %}</p>
<form method="post" action="{{ url_for('web.logout') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn alt">Log out</button></form>""",
        is_admin=is_admin_user())


# ---- news / giveaways
@bp.get("/news")
def news():
    rows = db().execute("SELECT * FROM news_posts ORDER BY pinned DESC, id DESC LIMIT 50").fetchall()
    return page("News", """<h2>News & giveaways</h2>{% if not rows %}<p class=mut>Nothing yet — check back soon.</p>{% endif %}
{% for n in rows %}<div class="card" style="padding:16px;margin-bottom:12px"><span class="tag {{ 'g' if n['kind']=='giveaway' }}">{{ n['kind']|title }}</span>
<span class="mut" style="font-size:12px"> {{ n['created_at'][:10] }}</span><h3>{{ n['title'] }}</h3>
<p style="white-space:pre-wrap">{{ n['body'] }}</p></div>{% endfor %}""", rows=rows)


@bp.route("/news/new", methods=["GET", "POST"])
@login_required
def news_new():
    if not is_admin_user():
        abort(403)
    if request.method == "POST":
        title, body = request.form.get("title", "").strip()[:150], request.form.get("body", "").strip()[:4000]
        kind = request.form.get("kind", "news")
        if title and body:
            db().execute("INSERT INTO news_posts(title,body,kind,pinned) VALUES(?,?,?,?)",
                         (title, body, kind if kind in ("news", "giveaway", "offer") else "news",
                          1 if request.form.get("pinned") else 0))
            db().commit()
            return redirect(url_for("web.news"))
    return page("Post news", """<h2>Post an update</h2><form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>Type</label><select name="kind"><option value="news">News</option><option value="giveaway">Giveaway</option><option value="offer">Offer</option></select>
<label>Title</label><input name="title" required><label>Message</label><textarea name="body" rows="6" required></textarea>
<label><input type="checkbox" name="pinned" style="width:auto"> Pin to top</label><p><button class="btn">Publish</button></p></form>""")


@bp.get("/admin-check")
@login_required
def admin_check():
    found = bool(admin_emails())
    return page("Admin check", """<h2>Admin check</h2>
<p>ADMIN_EMAIL variable on the server: <b>{{ 'found' if found else 'NOT FOUND - add it in Railway Variables and redeploy' }}</b></p>
<p>Your account email matches it: <b>{{ 'YES - you are admin' if ok else 'NO' }}</b></p>
{% if found and not ok %}<p class="mut">Sign up (or log in) with exactly the email you put in ADMIN_EMAIL.</p>{% endif %}""",
                found=found, ok=is_admin_user())


@bp.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": CFG.get("NAME", "AD Fashion").title(), "short_name": "AD Fashion",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#fbf7f1", "theme_color": "#5b2a54",
        "description": "Hijabs, jilbabs, textiles and more.",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json")


@bp.get("/sw.js")
def service_worker():
    js = """
const CACHE='ad-shell-v1';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim());});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(fetch(e.request).then(r=>{
    if(r.ok && (e.request.url.includes('/img/'))){const c=r.clone();caches.open(CACHE).then(k=>k.put(e.request,c));}
    return r;
  }).catch(()=>caches.match(e.request).then(m=>m||new Response('You are offline. Please reconnect to keep shopping.',{status:503,headers:{'Content-Type':'text/plain'}}))));
});
"""
    r = Response(js, mimetype="application/javascript")
    r.headers["Service-Worker-Allowed"] = "/"
    return r


_icons = {}


def _make_icon(size):
    if size in _icons:
        return _icons[size]
    import io
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (size, size), "#5b2a54")
        d = ImageDraw.Draw(im)
        m = size // 8
        d.ellipse([m, m, size - m, size - m], outline="#d9a948", width=max(4, size // 40))
        try:
            font = ImageFont.load_default(size=size // 3)
        except TypeError:
            font = ImageFont.load_default()
        box = d.textbbox((0, 0), "AD", font=font)
        d.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), "AD", fill="#fbf7f1", font=font)
        buf = io.BytesIO(); im.save(buf, "PNG"); data = buf.getvalue()
    except Exception:
        import struct, zlib
        rows = []
        c0, r0, cx = (0x5b, 0x2a, 0x54), size / 2, size / 2
        for y in range(size):
            row = bytearray([0])
            for x in range(size):
                dist = ((x - cx) ** 2 + (y - cx) ** 2) ** 0.5
                col = (0xd9, 0xa9, 0x48) if r0 * 0.55 < dist < r0 * 0.68 else c0
                row += bytes(col)
            rows.append(bytes(row))
        def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
    _icons[size] = data
    return data


@bp.get("/icon-<int:size>.png")
def icon(size):
    if size not in (192, 512):
        abort(404)
    r = Response(_make_icon(size), mimetype="image/png")
    r.headers["Cache-Control"] = "public, max-age=604800"
    return r


@bp.get("/health")
def health():
    return {"status": "ok", "service": CFG.get("NAME"), "web": True}


@bp.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /account\nDisallow: /order\nDisallow: /checkout\n", mimetype="text/plain")


def register(flask_app, cfg):
    CFG.update(cfg)
    flask_app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
        ("ad-web-" + cfg["BOT_TOKEN"]).encode()).hexdigest()
    flask_app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                            SESSION_COOKIE_SECURE=bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("SITE_URL")),
                            PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30, MAX_CONTENT_LENGTH=8 * 1024 * 1024)
    init_web_db()
    flask_app.register_blueprint(bp)
