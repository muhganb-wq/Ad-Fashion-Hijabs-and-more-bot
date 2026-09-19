import os
import sqlite3
import asyncio
import logging
from threading import Thread

from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# AD FASHION HIJABS & MORE — TELEGRAM SHOP BOT
# PHASE 7: Customer Experience, Operations, Retention, Support & Growth
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6921853187"))
PORT = int(os.environ.get("PORT", "10000"))

NAME = "AD FASHION HIJABS & MORE"
TAGLINE = "MODESTY • ELEGANCE • QUALITY"
PHONE1 = "09136114700"
PHONE2 = "07011927516"
WEBSITE = "https://adfashionhijabs.netlify.app/"
ADDRESS = "Bauchi Central Market, Bauchi, Nigeria"
WHATSAPP = "2349136114700"
DB = os.environ.get("DB_PATH", "shop.db")

# Shared fabric/color photo selection applies ONLY to these two categories.
COLOR_FABRIC_CATEGORIES = {"hijabs", "jilbabs"}

# Payment accounts
PAYMENT_ACCOUNTS = [
    ("OPAY", "MUHAMMAD MUHAMMAD ADAMU", "7011927516"),
    ("OPAY", "MUHAMMAD ADAMU", "9136114700"),
    ("POLARIS BANK", "MUHAMMAD ADAMU", "3095751555"),
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global Telegram error handler with the real exception traceback."""
    error = context.error
    logger.error("UNHANDLED TELEGRAM EXCEPTION: %r", error, exc_info=error)
    if update is not None:
        logger.error("Update that caused the exception: %r", update)


async def safe_edit_query(query, text, **kwargs):
    """Edit either a normal text message or a Telegram photo message safely.

    Customer color/fabric buttons are attached to photo messages. Telegram raises
    BadRequest('There is no text in the message to edit') when edit_message_text
    is used on those messages, so use the caption editor for photos.

    Also swallows BadRequest('Message is not modified') — a benign race that
    happens when a button (e.g. ➕/➖) is tapped twice in quick succession: the
    second request tries to set content identical to what the first tap already
    produced. The message already shows the correct thing, so there's nothing
    to fix; raising here would surface as a crash for no real problem.

    For any OTHER edit failure (the original message is too old for Telegram
    to edit, was deleted, or is some message type this bot doesn't expect),
    fall back to sending a brand-new message with the same content instead of
    raising. A user should see the screen they asked for, even if editing the
    old one isn't possible — that's better than a generic error screen for a
    problem that has nothing to do with the data being shown.
    """
    message = getattr(query, "message", None)
    is_photo_or_video = message is not None and (getattr(message, "photo", None) or getattr(message, "video", None))
    try:
        if is_photo_or_video:
            return await query.edit_message_caption(caption=text, **kwargs)
        return await query.edit_message_text(text=text, **kwargs)
    except BadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return None

        logger.warning("safe_edit_query: edit failed (%s) — sending a fresh message instead.", e)

        chat_id = None
        if message is not None and getattr(message, "chat_id", None):
            chat_id = message.chat_id
        elif getattr(query, "from_user", None):
            chat_id = query.from_user.id

        if chat_id is None:
            raise

        # A caption-only kwarg set (from the photo/video branch) isn't valid
        # for send_message; send_message only needs the text-message kwargs.
        send_kwargs = {k: v for k, v in kwargs.items() if k in ("parse_mode", "reply_markup")}
        try:
            bot = None
            try:
                bot = query.get_bot()
            except AttributeError:
                if message is not None:
                    bot = message.get_bot()
            if bot is None:
                raise RuntimeError("No bot instance available for fallback send_message.")
            return await bot.send_message(chat_id=chat_id, text=text, **send_kwargs)
        except Exception:
            logger.exception("safe_edit_query: fallback send_message also failed.")
            raise



# -------------------- DATABASE --------------------

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = conn()

    c.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER,
            description TEXT,
            variety TEXT DEFAULT '',
            features TEXT DEFAULT '',
            photo_file_id TEXT,
            video_file_id TEXT DEFAULT '',
            active INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            customer_name TEXT,
            phone TEXT,
            address TEXT,
            items TEXT,
            total INTEGER,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Phase 3 payment fields. Safe for existing databases.
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(orders)").fetchall()}
    product_cols = {row[1] for row in c.execute("PRAGMA table_info(products)").fetchall()}
    if "variety" not in product_cols:
        c.execute("ALTER TABLE products ADD COLUMN variety TEXT DEFAULT ''")
    if "features" not in product_cols:
        c.execute("ALTER TABLE products ADD COLUMN features TEXT DEFAULT ''")
    if "video_file_id" not in product_cols:
        c.execute("ALTER TABLE products ADD COLUMN video_file_id TEXT DEFAULT ''" )
    if "subcategory" not in product_cols:
        c.execute("ALTER TABLE products ADD COLUMN subcategory TEXT DEFAULT ''")
    if "payment_notified" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN payment_notified INTEGER DEFAULT 0")
    if "payment_method" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT ''")
    if "receipt_file_id" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN receipt_file_id TEXT DEFAULT ''")

    # Phase 4 delivery fields. Safe for existing databases.
    if "delivery_method" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN delivery_method TEXT DEFAULT ''")
    if "city" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN city TEXT DEFAULT ''")
    if "state" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN state TEXT DEFAULT ''")
    if "delivery_notes" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN delivery_notes TEXT DEFAULT ''")
    if "product_ids" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN product_ids TEXT DEFAULT ''")
    if "referrer_id" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN referrer_id INTEGER")
    if "delivery_attempts" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN delivery_attempts INTEGER DEFAULT 0")
    if "delivery_updated_at" not in existing_cols:
        c.execute("ALTER TABLE orders ADD COLUMN delivery_updated_at TIMESTAMP")

    # Customer CRM / retention data.
    c.execute("""
        CREATE TABLE IF NOT EXISTS customers(
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT DEFAULT '',
            referrer_id INTEGER,
            orders_count INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE,
            user_id INTEGER,
            rating INTEGER,
            comment TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            message TEXT NOT NULL,
            status TEXT DEFAULT 'OPEN',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS style_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            customer_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'NEW',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS style_request_media(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            file_id TEXT NOT NULL
        )
    """)
    style_cols = {row[1] for row in c.execute("PRAGMA table_info(style_requests)").fetchall()}
    for col, ddl in [
        ("product_price", "ALTER TABLE style_requests ADD COLUMN product_price INTEGER DEFAULT 0"),
        ("delivery_method", "ALTER TABLE style_requests ADD COLUMN delivery_method TEXT DEFAULT ''"),
        ("delivery_fee", "ALTER TABLE style_requests ADD COLUMN delivery_fee INTEGER DEFAULT 0"),
        ("total_price", "ALTER TABLE style_requests ADD COLUMN total_price INTEGER DEFAULT 0"),
        ("quote_status", "ALTER TABLE style_requests ADD COLUMN quote_status TEXT DEFAULT 'NEGOTIATING'"),
        ("order_id", "ALTER TABLE style_requests ADD COLUMN order_id INTEGER"),
    ]:
        if col not in style_cols:
            c.execute(ddl)
    c.execute("""
        CREATE TABLE IF NOT EXISTS style_request_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            sender_role TEXT NOT NULL,
            sender_id INTEGER,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS campaigns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            message TEXT NOT NULL,
            sent INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS order_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER DEFAULT 0
        )
    """)

    # Phase 5 settings store (key/value).
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Global color/fabric photo library. Admin can upload photos without naming them.
    c.execute("""
        CREATE TABLE IF NOT EXISTS color_options(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_file_id TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Color/fabric selection fields for order items. Safe for existing databases.
    order_item_cols = {row[1] for row in c.execute("PRAGMA table_info(order_items)").fetchall()}
    if "color_id" not in order_item_cols:
        c.execute("ALTER TABLE order_items ADD COLUMN color_id INTEGER")
    if "color_photo_file_id" not in order_item_cols:
        c.execute("ALTER TABLE order_items ADD COLUMN color_photo_file_id TEXT DEFAULT ''")
    c.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('accepting_orders', '1')"
    )

    starter = [
        (
            "royal_blue_hijab",
            "Royal Blue Hijab",
            "hijabs",
            None,
            "Elegant royal blue hijab. Price on request.",
        ),
        (
            "ismat_jilbab",
            "Ismat Jilbab",
            "jilbabs",
            24000,
            "Free-size maxi jilbab. Elegant, comfortable and modest.",
        ),
    ]

    for product in starter:
        c.execute(
            """
            INSERT OR IGNORE INTO products
            (id, name, category, price, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            product,
        )

    c.commit()
    c.close()


def money(value):
    if value is None:
        return "Price on request"
    return f"₦{value:,.0f}"


def fmt_status(status):
    """Renders a status code for display inside Markdown-formatted text.
    Telegram's legacy Markdown treats a bare underscore as an italics
    marker, so AWAITING_PAYMENT / OUT_FOR_DELIVERY etc. would otherwise
    break message parsing. Replace underscores with spaces for display.
    """
    return (status or "").replace("_", " ")


def md_escape(text):
    """Escapes legacy-Markdown special characters in arbitrary text
    (usernames, customer names, addresses, free-text messages, etc.)
    before it's interpolated into a ParseMode.MARKDOWN message.
    Telegram usernames routinely contain underscores, and customer-
    supplied text can contain any of these characters — left unescaped,
    a single one breaks parsing and the ENTIRE message silently fails
    to send. Legacy Markdown only treats _, *, `, [ as special.
    """
    if text is None:
        return ""
    text = str(text)
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def get_products(category=None, subcategory=None):
    c = conn()
    if category and subcategory:
        rows = c.execute(
            """
            SELECT * FROM products
            WHERE active=1 AND category=? AND subcategory=?
            ORDER BY name
            """,
            (category, subcategory),
        ).fetchall()
    elif category:
        rows = c.execute(
            """
            SELECT * FROM products
            WHERE active=1 AND category=?
            ORDER BY name
            """,
            (category,),
        ).fetchall()
    else:
        rows = c.execute(
            """
            SELECT * FROM products
            WHERE active=1
            ORDER BY name
            """
        ).fetchall()
    c.close()
    return rows


def get_product(product_id):
    c = conn()
    row = c.execute(
        "SELECT * FROM products WHERE id=?",
        (product_id,),
    ).fetchone()
    c.close()
    return row


def get_setting(key, default=None):
    c = conn()
    row = c.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,),
    ).fetchone()
    c.close()
    return row["value"] if row else default


def set_setting(key, value):
    c = conn()
    c.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    c.commit()
    c.close()


def is_accepting_orders():
    return get_setting("accepting_orders", "1") == "1"


DELIVERY_LABELS = {
    "pickup": "📍 Pickup — Bauchi Central Market",
    "bauchi": "🛵 Bauchi Delivery",
    "nationwide": "📦 Nationwide Delivery",
}

CATEGORY_LABELS = {
    "hijabs": "🧕 Hijabs",
    "jilbabs": "👗 Jilbabs",
    "textiles": "🧵 Textiles",
    "more": "✨ More",
}

SUBCATEGORY_MAP = {
    "textiles": [
        ("shadda", "Shadda"),
        ("cashmere", "Cashmere"),
        ("swiss", "Swiss"),
        ("osaka", "Osaka"),
        ("plain_swiss", "Plain Swiss"),
    ],
    "more": [
        ("shoes_male", "👞 Shoes (Male)"),
        ("shoes_female", "👠 Shoes (Female)"),
        ("caps_male", "🧢 Caps (Male)"),
        ("caps_female", "🧢 Caps (Female)"),
        ("watches", "⌚ Watches"),
        ("goggles", "🕶️ Goggles"),
    ],
}


def subcategory_label(category, subcategory):
    for key, label in SUBCATEGORY_MAP.get(category, []):
        if key == subcategory:
            return label
    return subcategory or ""


def get_delivery_fee(method):
    """Admin-configurable flat delivery fee per method, in naira. Defaults to 0."""
    try:
        return int(get_setting(f"delivery_fee_{method}", "0") or "0")
    except (ValueError, TypeError):
        return 0


def set_delivery_fee(method, amount):
    set_setting(f"delivery_fee_{method}", str(int(amount)))


def order_grand_total(items_total, method):
    """Adds the delivery fee to the item total. Returns None if items_total is
    None (a price-on-request item is in the cart) since the true total can't
    be computed yet — the delivery fee is shown separately in that case."""
    if items_total is None:
        return None
    return items_total + get_delivery_fee(method)


# -------------------- HELPERS --------------------

def is_admin(update):
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Shop Products", callback_data="shop")],
        [
            InlineKeyboardButton("🧕 Hijabs", callback_data="cat:hijabs"),
            InlineKeyboardButton("👗 Jilbabs", callback_data="cat:jilbabs"),
        ],
        [
            InlineKeyboardButton("🧵 Textiles", callback_data="cat:textiles"),
            InlineKeyboardButton("✨ More", callback_data="cat:more"),
        ],
        [
            InlineKeyboardButton("🛒 My Cart", callback_data="cart"),
            InlineKeyboardButton("📦 My Orders", callback_data="orders"),
        ],
        [
            InlineKeyboardButton("❓ Help & FAQ", callback_data="help"),
            InlineKeyboardButton("🎧 Support", callback_data="support"),
        ],
        [
            InlineKeyboardButton("📞 Contact Us", callback_data="contact"),
            InlineKeyboardButton("📍 Location", callback_data="location"),
        ],
        [InlineKeyboardButton("✨ Send My Own Style", callback_data="style")],
        [InlineKeyboardButton("🎁 Refer a Friend", callback_data="referral")],
        [InlineKeyboardButton("🌐 Visit Website", url=WEBSITE)],
    ])

def back_menu(callback_data="home"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data=callback_data)],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home")],
    ])


def category_menu(category, subcategory=None):
    rows = get_products(category, subcategory)
    buttons = []

    for product in rows:
        buttons.append([
            InlineKeyboardButton(
                f"{product['name']} — {money(product['price'])}",
                callback_data=f"product:{product['id']}",
            )
        ])

    if not rows:
        buttons.append([
            InlineKeyboardButton(
                "No products available yet",
                callback_data="shop",
            )
        ])

    if subcategory and category in SUBCATEGORY_MAP:
        buttons.append([
            InlineKeyboardButton("⬅️ Back", callback_data=f"cat:{category}")
        ])
    buttons.append([
        InlineKeyboardButton("🏠 Main Menu", callback_data="home")
    ])

    return InlineKeyboardMarkup(buttons)


def subcategory_menu(category):
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"cat:{category}:{key}")]
        for key, label in SUBCATEGORY_MAP.get(category, [])
    ]
    buttons.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def get_color_options():
    c = conn()
    rows = c.execute("SELECT * FROM color_options WHERE active=1 ORDER BY id ASC").fetchall()
    c.close()
    return rows


def get_color_option(color_id):
    c = conn()
    row = c.execute("SELECT * FROM color_options WHERE id=? AND active=1", (color_id,)).fetchone()
    c.close()
    return row


def cart_key(product_id, color_id=None):
    return f"{product_id}|{color_id}" if color_id is not None else str(product_id)


def split_cart_key(key):
    value = str(key)
    if "|" in value:
        product_id, color_id = value.rsplit("|", 1)
        try:
            return product_id, int(color_id)
        except ValueError:
            return product_id, None
    return value, None


def cart_product_color(key):
    product_id, color_id = split_cart_key(key)
    return get_product(product_id), get_color_option(color_id) if color_id is not None else None


def cart_total(cart):
    total = 0
    unknown = False

    for key, quantity in cart.items():
        product, _ = cart_product_color(key)
        if not product:
            continue
        if product["price"] is None:
            unknown = True
        else:
            total += product["price"] * quantity

    return None if unknown else total


def cart_lines(cart):
    lines = []
    total = 0
    unknown = False

    for key, quantity in cart.items():
        product, color = cart_product_color(key)
        if not product:
            continue

        if product["price"] is None:
            subtotal = "Price on request"
            unknown = True
        else:
            subtotal_value = product["price"] * quantity
            subtotal = money(subtotal_value)
            total += subtotal_value

        color_text = "" if not color else f"\n  🎨 Selected fabric/color: Photo #{color['id']}"
        lines.append(f"• {product['name']} × {quantity} — {subtotal}{color_text}")

    return lines, (None if unknown else total)


def cart_keyboard(cart):
    """Cart view keyboard with per-item ➖/🔢/➕ quantity controls."""
    rows = []
    for key, quantity in cart.items():
        product, _ = cart_product_color(key)
        name = product["name"] if product else "Item"
        short_name = name if len(name) <= 18 else name[:17] + "…"
        rows.append([
            InlineKeyboardButton("➖", callback_data=f"cartdec:{key}"),
            InlineKeyboardButton(f"🔢 {quantity}", callback_data=f"cartsetqty:{key}"),
            InlineKeyboardButton("➕", callback_data=f"cartinc:{key}"),
        ])
        rows.append([InlineKeyboardButton(f"🗑️ Remove {short_name}", callback_data=f"cartdel:{key}")])

    rows.append([InlineKeyboardButton("🧾 Checkout", callback_data="checkout")])
    rows.append([InlineKeyboardButton("🗑️ Clear Cart", callback_data="clear")])
    rows.append([InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop")])
    return InlineKeyboardMarkup(rows)


def item_selected_text(product, qty, color_id=None):
    subtotal = (product["price"] or 0) * qty
    lines = ["🧾 *Item Selected*", "", f"Product: *{product['name']}*"]
    if color_id is not None:
        lines.append(f"🎨 Fabric/Color: Photo #{color_id}")
    lines.append(f"🔢 Quantity: *{qty}*")
    if product["price"] is not None:
        lines.append(f"💰 Subtotal: *{money(subtotal)}*")
    lines.append("")
    lines.append("Tap ➖/➕ to adjust, or the number to type an exact quantity.")
    return "\n".join(lines)


def item_selected_keyboard(product_id, qty, color_id=None):
    """Live ➖/🔢/➕ quantity adjuster shown right after picking a product
    (or a color/fabric), so customers never need a separate 'change quantity'
    step — matches the same interaction style as the cart."""
    qty = max(1, qty)
    if color_id is not None:
        dec_cb = f"qtydec:{product_id}:{color_id}:{qty}"
        inc_cb = f"qtyinc:{product_id}:{color_id}:{qty}"
        type_cb = f"qtytype:{product_id}:{color_id}"
        add_cb = f"addcart:{product_id}:{color_id}:{qty}"
        order_cb = f"ordernow:{product_id}:{color_id}:{qty}"
        back_cb = f"colors:{product_id}"
        back_label = "🎨 Change Color"
    else:
        dec_cb = f"plainqtydec:{product_id}:{qty}"
        inc_cb = f"plainqtyinc:{product_id}:{qty}"
        type_cb = f"plainqtytype:{product_id}"
        add_cb = f"plainadd:{product_id}:{qty}"
        order_cb = f"plainordernow:{product_id}:{qty}"
        back_cb = f"product:{product_id}"
        back_label = "⬅️ Back to Product"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=dec_cb),
            InlineKeyboardButton(f"🔢 {qty}", callback_data=type_cb),
            InlineKeyboardButton("➕", callback_data=inc_cb),
        ],
        [InlineKeyboardButton("🛒 Add to Cart", callback_data=add_cb)],
        [InlineKeyboardButton("⚡ Order Now", callback_data=order_cb)],
        [InlineKeyboardButton(back_label, callback_data=back_cb)],
    ])


def clear_checkout_data(context):
    for key in [
        "checkout",
        "cart",
        "customer_name",
        "phone",
        "address",
        "delivery_method",
        "city",
        "state",
        "delivery_notes",
        "pending_order",
        "selected_product",
        "selected_color",
    ]:
        context.user_data.pop(key, None)


# -------------------- CUSTOMER CRM / RETENTION HELPERS --------------------

def upsert_customer(user, source="", referrer_id=None):
    if not user:
        return
    c = conn()
    row = c.execute("SELECT * FROM customers WHERE user_id=?", (user.id,)).fetchone()
    if row:
        c.execute("""UPDATE customers SET username=?, full_name=?, last_seen=CURRENT_TIMESTAMP,
                    phone=CASE WHEN ?!='' THEN ? ELSE phone END,
                    source=CASE WHEN ?!='' THEN ? ELSE source END
                    WHERE user_id=?""",
                  (user.username or '', user.full_name or '', '', '', source, source, user.id))
    else:
        c.execute("""INSERT INTO customers(user_id, username, full_name, source, referrer_id)
                     VALUES(?,?,?,?,?)""",
                  (user.id, user.username or '', user.full_name or '', source or '', referrer_id))
    c.commit(); c.close()


def customer_stats(user_id):
    c=conn(); row=c.execute("SELECT * FROM customers WHERE user_id=?", (user_id,)).fetchone(); c.close(); return row


def bot_username(context):
    return getattr(context.bot, "username", None) or "Ad_Fashion_Hijabs_and_morebot"


def referral_link(context, user_id):
    return f"https://t.me/{bot_username(context)}?start=ref_{user_id}"


async def send_retention_followup(context, row):
    order_id=row["id"]; order_number=f"AD-{order_id:05d}"
    text=(f"🎉 *Thank you for shopping with {NAME}!*\n\n"
          f"Order #{order_number} has been marked as delivered.\n\n"
          "We'd love to hear about your experience. Tap a rating below, or refer a friend and earn our appreciation as a returning customer.")
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ 1", callback_data=f"review:{order_id}:1"), InlineKeyboardButton("⭐ 2", callback_data=f"review:{order_id}:2"), InlineKeyboardButton("⭐ 3", callback_data=f"review:{order_id}:3"), InlineKeyboardButton("⭐ 4", callback_data=f"review:{order_id}:4"), InlineKeyboardButton("⭐ 5", callback_data=f"review:{order_id}:5")],
        [InlineKeyboardButton("🛍️ Shop Again", callback_data="shop"), InlineKeyboardButton("🎁 Refer a Friend", callback_data="referral")],
        [InlineKeyboardButton("🎧 Support", callback_data="support")],
    ])
    try:
        await context.bot.send_message(chat_id=row["user_id"], text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception:
        logger.exception("Could not send retention follow-up for %s", order_number)


# -------------------- CUSTOMER STYLE SERVICE --------------------

STYLE_DELIVERY_LABELS = {
    "pickup": "📍 Pickup — Bauchi Central Market",
    "bauchi_home": "🏠 Home Delivery — Bauchi",
    "national": "🇳🇬 Nationwide Delivery — Nigeria",
    "international": "🌍 International Delivery",
    "intercontinental": "🌎 Intercontinental Delivery",
    "other": "➕ Other Delivery Arrangement",
}


def active_style_for_user(user_id):
    c=conn(); row=c.execute(
        "SELECT * FROM style_requests WHERE user_id=? AND status NOT IN ('COMPLETED','CLOSED') ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone(); c.close(); return row


def style_customer_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚚 Choose Delivery", callback_data=f"style:delivery:{request_id}")],
        [InlineKeyboardButton("💬 Continue Chat", callback_data=f"style:chat:{request_id}")],
        [InlineKeyboardButton("💬 Switch to WhatsApp", url=f"https://wa.me/{WHATSAPP}")],
        [InlineKeyboardButton("❌ Close Request", callback_data=f"style:close:{request_id}")],
    ])


async def notify_style_admin(context, request_id, text, include_reply_button=True):
    c=conn(); row=c.execute("SELECT * FROM style_requests WHERE id=?", (request_id,)).fetchone(); c.close()
    if not row: return
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Reply to Customer", callback_data=f"adm:stylereply:{request_id}")],
                             [InlineKeyboardButton("💰 Set Product Price", callback_data=f"adm:styleprice:{request_id}"),
                              InlineKeyboardButton("🚚 Delivery", callback_data=f"adm:stylequote:{request_id}")]]) if include_reply_button else None
    await context.bot.send_message(chat_id=ADMIN_ID,
        text=f"✨ *Style Request #AD-S{request_id:04d}*\n👤 {md_escape(row['customer_name'] or 'Customer')}\nTelegram ID: `{row['user_id']}`\n\n💬 {md_escape(text)}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def send_style_delivery_menu(target, request_id):
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Pickup — Bauchi Central Market", callback_data=f"style:setdelivery:{request_id}:pickup")],
        [InlineKeyboardButton("🏠 Home Delivery — Bauchi", callback_data=f"style:setdelivery:{request_id}:bauchi_home")],
        [InlineKeyboardButton("🇳🇬 Nationwide Delivery — Nigeria", callback_data=f"style:setdelivery:{request_id}:national")],
        [InlineKeyboardButton("🌍 International Delivery", callback_data=f"style:setdelivery:{request_id}:international")],
        [InlineKeyboardButton("🌎 Intercontinental Delivery", callback_data=f"style:setdelivery:{request_id}:intercontinental")],
        [InlineKeyboardButton("➕ Other Delivery Arrangement", callback_data=f"style:setdelivery:{request_id}:other")],
    ])
    await target.reply_text("🚚 *Choose your delivery method*\n\nYou choose the delivery method. Our customer service team will determine and add the delivery charge before payment.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def style_create_payment_order(update, context, request_id):
    user=update.effective_user
    c=conn(); r=c.execute("SELECT * FROM style_requests WHERE id=? AND user_id=?",(request_id,user.id)).fetchone(); c.close()
    if not r or r["quote_status"]!="CONFIRMED": return
    item=f"Custom Style #AD-S{request_id:04d} — {r['description'][:500]}"
    delivery=r["delivery_method"] or "pickup"
    address=ADDRESS if delivery=="pickup" else context.user_data.get("style_address", "")
    city=context.user_data.get("style_city", "")
    state=context.user_data.get("style_state", "")
    total=int(r["total_price"] or 0)
    c=conn(); cur=c.execute("""INSERT INTO orders(user_id,username,customer_name,phone,address,items,total,status,delivery_method,city,state,delivery_notes,product_ids) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user.id,user.username or '',user.full_name or '',context.user_data.get("style_phone", ""),address,item,total,"AWAITING_PAYMENT",delivery,city,state,"Custom style request",f"STYLE-{request_id}"))
    order_id=cur.lastrowid; c.execute("UPDATE style_requests SET order_id=?, status='ORDERED' WHERE id=?",(order_id,request_id)); c.commit(); c.close()
    order_no=f"AD-{order_id:05d}"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("💳 I've Made Payment", callback_data=f"payment_done:{order_id}")],[InlineKeyboardButton("🎧 Chat with Customer Service", callback_data=f"style:chat:{request_id}")]])
    await update.effective_message.reply_text(payment_instructions(order_no,total),parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
    await context.bot.send_message(chat_id=ADMIN_ID,text=f"🛒 *CUSTOM STYLE ORDER CREATED*\nStyle Request: #AD-S{request_id:04d}\nOrder: *{order_no}*\nCustomer: {md_escape(user.full_name)}\nTotal: *{money(total)}*\nDelivery: {STYLE_DELIVERY_LABELS.get(delivery,delivery)}",parse_mode=ParseMode.MARKDOWN)


# -------------------- USER COMMANDS --------------------

DEFAULT_WELCOME_MESSAGE = (
    "Welcome to your online modest-fashion store.\n\n"
    "🛍️ Browse products\n🛒 Add to cart\n💳 Pay securely\n🚚 Choose delivery or pickup\n🎧 Get support anytime\n\n"
    "Choose an option below:"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user
    args=context.args or []
    source="telegram"
    referrer_id=None
    if args and args[0].startswith("ref_"):
        try:
            candidate=int(args[0].split("_",1)[1])
            if candidate != user.id:
                referrer_id=candidate
                source="referral"
        except ValueError:
            pass
    upsert_customer(user, source=source, referrer_id=referrer_id)
    stats=customer_stats(user.id)
    vip_line="\n👑 *VIP / Returning Customer* — welcome back!" if stats and stats["is_vip"] else ""
    welcome_body = get_setting("welcome_message", DEFAULT_WELCOME_MESSAGE)
    text=(f"🖤 *{NAME}*\n\n*{TAGLINE}*{vip_line}\n\n" + welcome_body)
    welcome_photo = get_setting("welcome_photo_file_id", "")
    if update.message:
        sent = False
        if welcome_photo and len(text) <= 1024:
            try:
                await update.message.reply_photo(
                    photo=welcome_photo,
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=main_menu(),
                )
                sent = True
            except Exception:
                logger.exception("Could not send merged welcome photo+caption — falling back to text only.")
        elif welcome_photo:
            # Caption would be too long for Telegram's 1024-char limit; send
            # the photo first, then the full message as its own message.
            try:
                await update.message.reply_photo(photo=welcome_photo)
            except Exception:
                logger.exception("Could not send welcome photo — falling back to text only.")

        if not sent:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message or update.effective_message
    await target.reply_text(
        "🛍️ *Choose a collection:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "❓ *AD Fashion Hijabs & More — Help Center*\n\n"
        "🛍️ *Shopping:* /shop to browse and select products.\n"
        "🛒 *Cart:* /cart to review items before checkout.\n"
        "📦 *Orders:* /orders to view your order history.\n"
        "💳 *Payment:* After checkout, follow the payment instructions and tap ‘I've Made Payment’.\n"
        "🚚 *Delivery:* Choose pickup, Bauchi delivery or nationwide delivery during checkout.\n"
        "🎥 *Product videos:* available on product pages where uploaded.\n"
        "✨ *Send Your Style:* send us your own sample photo/video and tell us what you want.\n"
        "🎧 *Support:* /support to contact our team.\n"
        "❔ *FAQ:* /faq for common questions.\n\n"
        "To cancel an active checkout, send *cancel*.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())


async def faq_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "❔ *Frequently Asked Questions*\n\n"
        "*How do I order?*\nBrowse → Select → Cart → Checkout → Payment → Verification → Delivery.\n\n"
        "*How do I pay?*\nThe bot displays our available payment accounts after an order is created. Use the order number as your reference where possible.\n\n"
        "*Can I pick up my order?*\nYes. Pickup is available at Bauchi Central Market.\n\n"
        "*Do you deliver outside Bauchi?*\nYes. Select Nationwide Delivery and provide your city and state.\n\n"
        "*How do I check my order?*\nUse /orders.\n\n"
        "*Need human assistance?*\nUse /support or contact us directly.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["support_ticket"]="message"
    await update.effective_message.reply_text(
        "🎧 *Customer Support*\n\nTell us what you need help with in one message.\n\n"
        "Examples: payment issue, delivery issue, wrong item, order question, complaint, or general enquiry.\n\n"
        "Our team will review your request.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="support_cancel")]]))


async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; upsert_customer(user, source="referral")
    link=referral_link(context,user.id)
    await update.effective_message.reply_text(
        "🎁 *Refer a Friend*\n\n"
        "Share your personal shopping link with a friend. When they start the bot through your link, we'll record the referral.\n\n"
        f"🔗 {link}\n\n"
        "Thank you for helping AD Fashion Hijabs & More grow. 🖤✨",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args=context.args or []
    if len(args)<2:
        await update.effective_message.reply_text("Use: /review ORDER_NUMBER RATING\nExample: /review AD-00001 5")
        return
    order_ref=args[0].upper().replace("AD-","")
    try: order_id=int(order_ref); rating=int(args[1])
    except ValueError:
        await update.effective_message.reply_text("Please provide a valid order number and rating from 1 to 5."); return
    if rating<1 or rating>5:
        await update.effective_message.reply_text("Rating must be between 1 and 5."); return
    c=conn(); row=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,update.effective_user.id)).fetchone()
    if not row:
        c.close(); await update.effective_message.reply_text("Order not found."); return
    c.execute("INSERT INTO reviews(order_id,user_id,rating,comment) VALUES(?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET rating=excluded.rating",(order_id,update.effective_user.id,rating,"")); c.commit(); c.close()
    await update.effective_message.reply_text("⭐ Thank you! Your rating has been recorded. You can send a short comment if you'd like.", reply_markup=main_menu())


async def cart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_cart(update, context)


async def send_order_status_notification(context, user_id, order_number, status):
    """Send a consistent customer-facing status notification."""
    messages = {
        "PAID": "💳 Payment verified successfully. Your order is now confirmed and ready for preparation.",
        "PROCESSING": "🛠️ Your order is now being prepared.",
        "OUT_FOR_DELIVERY": "🛵 Your order is on the way to you. Please keep your phone available for the rider/courier.",
        "DELIVERY_FAILED": "⚠️ We could not complete the delivery attempt. Our team will contact you to arrange the next step.",
        "DELIVERED": "🎉 Your order has been marked as delivered. Thank you for shopping with us!",
        "CANCELLED": "❌ Your order has been cancelled. Please contact us if you need assistance.",
        "AWAITING_PAYMENT": "⏳ Your order is waiting for payment. Complete payment and notify us when done.",
        "PENDING": "📦 Your order is pending review.",
    }
    body = messages.get(status, f"📦 Your order status is now: {status.replace('_', ' ')}")
    try:
        keyboard=None
        if status == "DELIVERED":
            order_id=int(order_number.replace("AD-", ""))
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("⭐ Rate Order", callback_data=f"review:{order_id}:5"), InlineKeyboardButton("🛍️ Shop Again", callback_data="shop")], [InlineKeyboardButton("🎁 Refer a Friend", callback_data="referral")]])
        elif status == "OUT_FOR_DELIVERY":
            order_id=int(order_number.replace("AD-", ""))
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("✅ I Received My Order", callback_data=f"delivery_confirm:{order_id}:yes"), InlineKeyboardButton("❌ Not Received", callback_data=f"delivery_confirm:{order_id}:no")],[InlineKeyboardButton("🎧 Support", callback_data="support")]])
        elif status in {"AWAITING_PAYMENT", "PAID"}:
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("📦 My Orders", callback_data="orders")],[InlineKeyboardButton("🎧 Support", callback_data="support")]])
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📦 *Order #{order_number} Update*\n\n{body}\n\nStatus: *{status.replace('_', ' ')}*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard,
        )
        return True
    except Exception:
        logger.exception("Could not notify customer about order status change.")
        return False


async def verify_payment(context, order_id: int, approved: bool):
    """Admin payment verification. Returns the updated order or None."""
    c = conn()
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        c.close()
        return None

    if approved:
        new_status = "PAID"
        c.execute("UPDATE orders SET status=?, payment_method=? WHERE id=?", (new_status, "VERIFIED_BY_ADMIN", order_id))
    else:
        new_status = "AWAITING_PAYMENT"
        c.execute("UPDATE orders SET status=?, payment_notified=0 WHERE id=?", (new_status, order_id))
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"
    await send_order_status_notification(context, row["user_id"], order_number, new_status)
    if new_status == "DELIVERED":
        await send_retention_followup(context, row)
    return row


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a launch/promotion message to customers who have placed an order."""
    if not is_admin(update):
        return
    message = update.message.text.partition(" ")[2].strip()
    if not message:
        await update.message.reply_text(
            "📣 *Broadcast*\n\nUse:\n/broadcast Your announcement here\n\nExample:\n/broadcast 🖤 New arrivals are now available at AD Fashion Hijabs & More!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    campaign_name = "Broadcast " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    c = conn(); cur=c.execute("INSERT INTO campaigns(name,message) VALUES(?,?)",(campaign_name,message)); campaign_id=cur.lastrowid
    rows = c.execute("SELECT DISTINCT user_id FROM orders WHERE user_id IS NOT NULL").fetchall(); c.close()
    sent = 0
    failed = 0
    for row in rows:
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=f"📣 *{NAME}*\n\n{message}",
                parse_mode=ParseMode.MARKDOWN,
            )
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Broadcast failed for user %s", row["user_id"])
        await asyncio.sleep(0.05)

    c=conn(); c.execute("UPDATE campaigns SET sent=?, failed=? WHERE id=?",(sent,failed,campaign_id)); c.commit(); c.close()
    await update.message.reply_text(f"📣 Broadcast complete.\n\n✅ Sent: {sent}\n⚠️ Failed: {failed}\n\nCampaign recorded for performance tracking.")


async def campaigns_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    c=conn(); rows=c.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 10").fetchall(); c.close()
    if not rows: await update.message.reply_text("No campaigns recorded yet."); return
    lines=["📣 *Campaign Performance*\n"]
    for r in rows:
        total=r['sent']+r['failed']; rate=(r['sent']/total*100) if total else 0
        lines.append(f"*{r['name']}*\n✅ Sent: {r['sent']} | ⚠️ Failed: {r['failed']} | Delivery rate: {rate:.0f}%")
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def launch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    launch = (
        "🖤 *AD FASHION HIJABS & MORE — NOW ONLINE!*\n\n"
        "🛍️ Shop hijabs, jilbabs, textiles and more directly through our Telegram store.\n\n"
        "✨ Modesty • Elegance • Quality\n"
        "📍 Bauchi Central Market, Bauchi\n\n"
        "Tap /start to begin shopping."
    )
    await update.message.reply_text(
        launch,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛍️ Shop Now", callback_data="shop")],
            [InlineKeyboardButton("🌐 Website", url=WEBSITE)],
            [InlineKeyboardButton("💬 WhatsApp", url=f"https://wa.me/{WHATSAPP}")],
        ]),
    )


async def payment_done(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id_text: str):
    query = update.callback_query
    try:
        order_id = int(order_id_text)
    except ValueError:
        await safe_edit_query(query, "⚠️ Invalid order number.", reply_markup=main_menu())
        return

    c = conn()
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        c.close()
        await safe_edit_query(query, "⚠️ Order not found.", reply_markup=main_menu())
        return

    if row["user_id"] != update.effective_user.id:
        c.close()
        await safe_edit_query(query, "⚠️ This order does not belong to you.", reply_markup=main_menu())
        return

    if row["payment_notified"]:
        c.close()
        await safe_edit_query(query, 
            "✅ We already received your payment notification.",
            reply_markup=main_menu(),
        )
        return

    c.execute("UPDATE orders SET payment_notified=1, payment_method=? WHERE id=?", ("CUSTOMER_PAYMENT_NOTIFICATION", order_id))
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"
    username = update.effective_user.username or "none"
    admin_text = (
        f"💳 *PAYMENT NOTIFICATION — #{order_number}*\n\n"
        f"👤 Customer: {md_escape(row['customer_name'])}\n"
        f"Username: @{md_escape(username)}\n"
        f"Telegram ID: `{row['user_id']}`\n"
        f"📞 Phone: {md_escape(row['phone'])}\n"
        f"💰 Order Total: *{money(row['total'])}*\n"
        f"📦 Current Status: *{fmt_status(row['status'])}*\n\n"
        "⚠️ Customer says payment has been made. Please verify the bank transaction before marking the order as PAID."
    )
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Verify Payment", callback_data=f"adm:payverify:{order_id}:yes")],
                [InlineKeyboardButton("⚠️ Payment Not Verified", callback_data=f"adm:payverify:{order_id}:no")],
                [InlineKeyboardButton("📦 View Order", callback_data=f"adm:order:{order_id}")],
            ]),
        )
    except Exception:
        logger.exception("Could not send payment notification to admin.")

    await safe_edit_query(query, 
        f"✅ *Payment notification received for #{order_number}*\n\n"
        "Thank you. We will verify your payment and update your order status.",
        parse_mode=ParseMode.MARKDOWN,
    )

    context.user_data["awaiting_receipt"] = order_id

    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=(
            "🧾 *Speed up verification*\n\n"
            "If you have a screenshot of your transfer receipt, send it now "
            "as a photo and we'll match it to your order.\n\n"
            "Or type *skip* to continue without one."
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    c = conn()
    rows = c.execute(
        """
        SELECT * FROM orders
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (user_id,),
    ).fetchall()
    c.close()

    if not rows:
        text = "📦 *You have no orders yet.*"
    else:
        text = "📦 *Your recent orders*\n\n"
        text += "\n".join(
            f"#{row['id']} — {money(row['total'])} — {fmt_status(row['status'])}"
            for row in rows
        )

    if update.callback_query:
        await safe_edit_query(
            update.callback_query,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )


async def contact_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"📞 *{NAME}*\n\n"
        f"{PHONE1}\n"
        f"{PHONE2}\n\n"
        f"WhatsApp: https://wa.me/{WHATSAPP}\n"
        f"Website: {WEBSITE}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )


# -------------------- CART --------------------

async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", {})

    if not cart:
        text = "🛒 *Your cart is empty.*"
        keyboard = main_menu()
    else:
        lines, total = cart_lines(cart)

        text = "🛒 *Your Cart*\n\n"
        text += "\n".join(lines)
        text += "\n\n"
        text += f"*Total: {money(total)}*\n\n"
        text += "Use ➖/➕ below to adjust quantities."

        keyboard = cart_keyboard(cart)

    if update.callback_query:
        await safe_edit_query(
            update.callback_query,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
    else:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )


# -------------------- CHECKOUT / DELIVERY (PHASE 4) --------------------

async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", {})

    if not cart:
        if update.callback_query:
            await safe_edit_query(
                update.callback_query,
                "🛒 Your cart is empty.",
                reply_markup=main_menu(),
            )
        else:
            await update.effective_message.reply_text(
                "🛒 Your cart is empty.",
                reply_markup=main_menu(),
            )
        return

    if not is_accepting_orders():
        await update.effective_message.reply_text(
            "🚫 We're not accepting new orders right now. "
            "Please check back later or contact us directly.",
            reply_markup=main_menu(),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(DELIVERY_LABELS["pickup"], callback_data="delivery:pickup")],
        [InlineKeyboardButton(DELIVERY_LABELS["bauchi"], callback_data="delivery:bauchi")],
        [InlineKeyboardButton(DELIVERY_LABELS["nationwide"], callback_data="delivery:nationwide")],
    ])

    await update.effective_message.reply_text(
        "🚚 *Choose your delivery method:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


async def start_delivery_details(update: Update, context: ContextTypes.DEFAULT_TYPE, method: str):
    context.user_data["delivery_method"] = method
    context.user_data["checkout"] = "full_name"

    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=(
            "🧾 *Checkout — Step 1*\n\n"
            "What's the full name for this order?"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "phone"

    await update.message.reply_text(
        "🧾 *Checkout — Step 2*\n\n"
        "Please share your phone number with us.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(
            [[
                KeyboardButton(
                    "📱 Share Phone Number",
                    request_contact=True,
                )
            ]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "address"

    await update.message.reply_text(
        "🧾 *Checkout — Step 3*\n\n"
        "Now send your complete delivery address (street, house number, landmark).",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )


async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "city"

    await update.message.reply_text(
        "🧾 *Checkout — Step 4*\n\n"
        "Which city/town is this for?",
        parse_mode=ParseMode.MARKDOWN,
    )


async def ask_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "state"

    await update.message.reply_text(
        "🧾 *Checkout — Step 5*\n\n"
        "Which state?",
        parse_mode=ParseMode.MARKDOWN,
    )


async def ask_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "notes"

    method = context.user_data.get("delivery_method")
    prompt = (
        "Any preferred pickup time or note for us? (or type *none*)"
        if method == "pickup"
        else "Any additional delivery instructions? (or type *none*)"
    )

    await update.message.reply_text(
        f"🧾 *Checkout — Final Step*\n\n{prompt}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def show_order_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", {})
    name = context.user_data.get("customer_name", update.effective_user.full_name)
    phone = context.user_data.get("phone", "")
    method = context.user_data.get("delivery_method", "pickup")
    address = context.user_data.get("address", "")
    city = context.user_data.get("city", "")
    state = context.user_data.get("state", "")
    notes = context.user_data.get("delivery_notes", "")

    lines, total = cart_lines(cart)
    delivery_fee = get_delivery_fee(method)
    grand_total = order_grand_total(total, method)

    context.user_data["pending_order"] = True

    delivery_lines = [f"🚚 Delivery: {DELIVERY_LABELS.get(method, method)}"]
    if method == "pickup":
        delivery_lines.append(f"📍 Pickup point: {ADDRESS}")
    else:
        delivery_lines.append(f"📍 Address: {md_escape(address)}")
        delivery_lines.append(f"🏙️ City/Town: {md_escape(city)}")
        if state:
            delivery_lines.append(f"🗺️ State: {md_escape(state)}")
    if notes and notes.lower() != "none":
        delivery_lines.append(f"📝 Notes: {md_escape(notes)}")
    if delivery_fee:
        delivery_lines.append(f"🚚 Delivery Fee: {money(delivery_fee)}")

    text = (
        "🧾 *Confirm your order*\n\n"
        f"👤 Customer: {md_escape(name)}\n"
        f"📞 Phone: {md_escape(phone)}\n"
        + "\n".join(delivery_lines)
        + "\n\n*Items:*\n"
        + "\n".join(lines)
        + f"\n\n💰 *Total: {money(grand_total)}*"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Confirm Order",
                callback_data="confirm_order",
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="cancel_order",
            ),
        ],
        [InlineKeyboardButton("🛒 Back to Cart", callback_data="cart")],
    ])

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


def payment_instructions(order_number, total):
    lines = [
        "💳 *PAYMENT OPTIONS*",
        "",
        f"Order Reference: *{order_number}*",
        f"Amount to Pay: *{money(total)}*",
        "",
        "Please transfer the exact amount to any of the accounts below and use your order number as the payment reference where possible.",
        "",
    ]
    for i, (bank, account_name, account_number) in enumerate(PAYMENT_ACCOUNTS, 1):
        lines.extend([
            f"*Option {i}*",
            f"🏦 Bank: *{bank}*",
            f"👤 Account Name: *{account_name}*",
            f"🔢 Account Number: *{account_number}*",
            "",
        ])
    lines.append("After making payment, tap *💳 I've Made Payment* below to notify us.")
    return "\n".join(lines)


async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", {})

    if not cart:
        await update.effective_message.reply_text(
            "🛒 Your cart is empty.",
            reply_markup=main_menu(),
        )
        return

    user = update.effective_user
    username = user.username or ""
    customer_name = context.user_data.get(
        "customer_name",
        user.full_name,
    )
    phone = context.user_data.get("phone", "")
    method = context.user_data.get("delivery_method", "pickup")
    address = context.user_data.get("address", "") if method != "pickup" else ADDRESS
    city = context.user_data.get("city", "")
    state = context.user_data.get("state", "")
    notes = context.user_data.get("delivery_notes", "")
    if notes.lower() == "none":
        notes = ""

    lines, total = cart_lines(cart)
    items = "\n".join(lines)
    delivery_fee = get_delivery_fee(method)
    grand_total = order_grand_total(total, method)

    c = conn()

    cursor = c.execute(
        """
        INSERT INTO orders
        (user_id, username, customer_name, phone, address, items, total,
         status, delivery_method, city, state, delivery_notes, product_ids, referrer_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user.id,
            username,
            customer_name,
            phone,
            address,
            items,
            grand_total or 0,
            "AWAITING_PAYMENT",
            method,
            city,
            state,
            notes,
            ",".join(str(k) for k in cart.keys()),
            (customer_stats(user.id)["referrer_id"] if customer_stats(user.id) else None),
        ),
    )

    order_id = cursor.lastrowid
    for key, qty in cart.items():
        pid, color_id = split_cart_key(key)
        product = get_product(pid)
        color = get_color_option(color_id) if color_id is not None else None
        if product:
            c.execute("INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price,color_id,color_photo_file_id) VALUES(?,?,?,?,?,?,?)",
                      (order_id, pid, product["name"], qty, product["price"] or 0, color_id, color["photo_file_id"] if color else ""))
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"

    delivery_summary = [f"🚚 Delivery: {DELIVERY_LABELS.get(method, method)}"]
    if method == "pickup":
        delivery_summary.append(f"📍 Pickup point: {ADDRESS}")
    else:
        delivery_summary.append(f"📍 Address: {md_escape(address)}")
        delivery_summary.append(f"🏙️ City/Town: {md_escape(city)}")
        if state:
            delivery_summary.append(f"🗺️ State: {md_escape(state)}")
    if notes:
        delivery_summary.append(f"📝 Notes: {md_escape(notes)}")
    if delivery_fee:
        delivery_summary.append(f"🚚 Delivery Fee: {money(delivery_fee)}")

    admin_text = (
        f"🔔 *NEW ORDER #{order_number}*\n\n"
        f"👤 Customer: {md_escape(customer_name)}\n"
        f"Username: @{md_escape(username or 'none')}\n"
        f"Telegram ID: `{user.id}`\n"
        f"📞 Phone: {md_escape(phone)}\n"
        + "\n".join(delivery_summary)
        + f"\n\n*Items:*\n{items}\n\n"
        f"💰 *Total: {money(grand_total)}*\n"
        f"📦 Status: *AWAITING PAYMENT*\n\n"
        "💳 Customer should complete payment using the payment options shown by the bot."
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode=ParseMode.MARKDOWN,
        )
        # Send the actual selected fabric/color photos to admin so the requested
        # color can be identified visually, not only by an internal photo number.
        sent_color_ids = set()
        for key in cart.keys():
            _pid, color_id = split_cart_key(key)
            if color_id is None or color_id in sent_color_ids:
                continue
            color = get_color_option(color_id)
            if color:
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=color["photo_file_id"],
                    caption=f"🎨 Selected fabric/color for Order #{order_number} — Photo #{color_id}",
                )
                sent_color_ids.add(color_id)
    except Exception:
        logger.exception("Could not send admin order notification/color photo.")

    clear_checkout_data(context)

    payment_text = payment_instructions(order_number, grand_total)
    payment_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 I've Made Payment", callback_data=f"payment_done:{order_id}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home")],
    ])

    await update.effective_message.reply_text(
        f"✅ *Order #{order_number} received!*\n\n"
        "Your order has been created successfully.\n\n"
        + payment_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=payment_keyboard,
    )


# -------------------- TEXT / CONTACT HANDLER --------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    lower = text.lower()

    if lower == "cancel" and context.user_data.get("cart_qty_target"):
        context.user_data.pop("cart_qty_target", None)
        await show_cart(update, context)
        return

    if lower == "cancel" and context.user_data.get("shop_qty_color_target"):
        target = context.user_data.pop("shop_qty_color_target")
        product = get_product(target["product_id"])
        if product:
            await message.reply_text(
                f"Choose a color/fabric for *{product['name']}*, or use the buttons below.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎨 Choose Color / Fabric", callback_data=f"colors:{target['product_id']}")]]),
            )
        else:
            await message.reply_text("Choose an option:", reply_markup=main_menu())
        return

    if lower == "cancel" and context.user_data.get("shop_qty_target"):
        product_id = context.user_data.pop("shop_qty_target")
        product = get_product(product_id)
        if product:
            await message.reply_text(
                f"Choose quantity for *{product['name']}*, or use the buttons below.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔢 Choose Quantity", callback_data=f"plainqty:{product_id}")]]),
            )
        else:
            await message.reply_text("Choose an option:", reply_markup=main_menu())
        return

    if lower == "cancel":
        in_admin_flow = bool(
            context.user_data.get("admin_add") or context.user_data.get("admin_edit") or context.user_data.get("admin_color_add") or context.user_data.get("admin_edit_welcome") or context.user_data.get("admin_edit_fee") or context.user_data.get("admin_edit_welcome_photo")
        )
        context.user_data.clear()

        if in_admin_flow:
            await message.reply_text("❌ Cancelled.")
        else:
            await message.reply_text(
                "❌ Checkout cancelled.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.reply_text(
                "Choose an option:",
                reply_markup=main_menu(),
            )
        return

    if context.user_data.get("cart_qty_target"):
        key = context.user_data.pop("cart_qty_target")
        cart = context.user_data.get("cart", {})
        try:
            qty = int(text.replace(",", "").strip())
        except ValueError:
            await message.reply_text("⚠️ Please send a valid whole number (e.g. 2, 15, 30).")
            context.user_data["cart_qty_target"] = key
            return
        if qty <= 0:
            cart.pop(key, None)
        else:
            cart[key] = qty
        context.user_data["cart"] = cart
        await show_cart(update, context)
        return

    if context.user_data.get("shop_qty_color_target"):
        target = context.user_data.pop("shop_qty_color_target")
        product_id, color_id = target["product_id"], target["color_id"]
        product, color = get_product(product_id), get_color_option(color_id)
        if not product or not color:
            await message.reply_text("⚠️ Product or fabric/color is no longer available.", reply_markup=main_menu())
            return
        try:
            qty = int(text.replace(",", "").strip())
            if qty < 1:
                raise ValueError
        except ValueError:
            await message.reply_text("⚠️ Please send a valid whole number (e.g. 2, 15, 30).")
            context.user_data["shop_qty_color_target"] = target
            return

        context.user_data["pending_cart_item"] = {"product_id": product_id, "color_id": color_id, "qty": qty}
        context.user_data["selected_product"] = product_id
        context.user_data["selected_color"] = color_id

        await message.reply_text(
            item_selected_text(product, qty, color_id=color_id),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=item_selected_keyboard(product_id, qty, color_id=color_id),
        )
        return

    if context.user_data.get("shop_qty_target"):
        product_id = context.user_data.pop("shop_qty_target")
        product = get_product(product_id)
        if not product:
            await message.reply_text("⚠️ That product is no longer available.", reply_markup=main_menu())
            return
        try:
            qty = int(text.replace(",", "").strip())
            if qty < 1:
                raise ValueError
        except ValueError:
            await message.reply_text("⚠️ Please send a valid whole number (e.g. 2, 15, 30).")
            context.user_data["shop_qty_target"] = product_id
            return

        await message.reply_text(
            item_selected_text(product, qty),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=item_selected_keyboard(product_id, qty),
        )
        return

    if context.user_data.get("admin_edit_welcome_photo") and is_admin(update):
        await message.reply_text(
            "🖼️ Please send a *photo*, or type *cancel* to stop.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if context.user_data.get("admin_edit_welcome") and is_admin(update):
        context.user_data.pop("admin_edit_welcome", None)
        if lower == "reset":
            set_setting("welcome_message", DEFAULT_WELCOME_MESSAGE)
            await message.reply_text("✅ Welcome message reset to default.", reply_markup=admin_back_button())
        elif not text:
            await message.reply_text("⚠️ Please send some text.", reply_markup=admin_back_button())
        else:
            set_setting("welcome_message", text)
            await message.reply_text("✅ Welcome message updated.", reply_markup=admin_back_button())
        return

    if context.user_data.get("admin_edit_fee") and is_admin(update):
        method = context.user_data.pop("admin_edit_fee")
        try:
            fee = int(text.replace(",", "").strip())
            if fee < 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "⚠️ Please send a valid non-negative number (e.g. 1000 or 0).",
                reply_markup=admin_back_button(),
            )
            return
        set_delivery_fee(method, fee)
        await message.reply_text(
            f"✅ {DELIVERY_LABELS.get(method, method)} fee set to {money(fee)}.",
            reply_markup=admin_back_button(),
        )
        return

    if context.user_data.get("awaiting_receipt"):
        if lower == "skip":
            context.user_data.pop("awaiting_receipt", None)
            await message.reply_text(
                "👍 No problem — we'll verify your payment from the bank record. "
                "You'll be notified once your order is confirmed.",
                reply_markup=main_menu(),
            )
        else:
            await message.reply_text(
                "📸 Please send your receipt as a *photo*, or type *skip* to continue without one.",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # ---- Admin panel: guided "add product" flow ----
    admin_add = context.user_data.get("admin_add")
    if admin_add and is_admin(update):
        try:
            await handle_admin_add_step(message, context, admin_add, text)
        except Exception:
            logger.exception("Unhandled error in guided add-product flow.")
            context.user_data.pop("admin_add", None)
            await message.reply_text(
                "⚠️ Something went wrong adding that product. Please start again with /admin.",
                reply_markup=admin_back_button(),
            )
        return

    # ---- Admin panel: guided "edit product" flow ----
    admin_edit = context.user_data.get("admin_edit")
    if admin_edit and is_admin(update):
        try:
            await handle_admin_edit_step(message, context, admin_edit, text)
        except Exception:
            logger.exception("Unhandled error in guided edit-product flow.")
            context.user_data.pop("admin_edit", None)
            await message.reply_text(
                "⚠️ Something went wrong updating that product. Please start again with /admin.",
                reply_markup=admin_back_button(),
            )
        return

    if context.user_data.get("review_order"):
        order_id=context.user_data.pop("review_order")
        c=conn(); c.execute("UPDATE reviews SET comment=? WHERE order_id=? AND user_id=?", (text[:500], order_id, update.effective_user.id)); c.commit(); c.close()
        await message.reply_text("❤️ Thank you for your feedback. We appreciate your support!", reply_markup=main_menu())
        return

    if context.user_data.get("style_request_step") == "description":
        style = context.user_data.get("style_request") or {"photos": [], "video": None}
        user = update.effective_user
        description = text[:1000]
        c=conn(); cur=c.execute("INSERT INTO style_requests(user_id,username,customer_name,description,status,quote_status) VALUES(?,?,?,?,?,?)", (user.id, user.username or '', user.full_name or '', description, "NEGOTIATING", "NEGOTIATING")); request_id=cur.lastrowid
        c.execute("INSERT INTO style_request_messages(request_id,sender_role,sender_id,message) VALUES(?,?,?,?)",(request_id,"customer",user.id,description))
        for fid in style.get("photos", []): c.execute("INSERT INTO style_request_media(request_id,media_type,file_id) VALUES(?,?,?)", (request_id,"photo",fid))
        if style.get("video"): c.execute("INSERT INTO style_request_media(request_id,media_type,file_id) VALUES(?,?,?)", (request_id,"video",style["video"]))
        c.commit(); c.close()
        try:
            await context.bot.send_message(chat_id=ADMIN_ID,text=f"✨ *NEW STYLE CUSTOMER CHAT #AD-S{request_id:04d}*\n\n👤 Customer: {md_escape(user.full_name)}\nUsername: @{md_escape(user.username or 'none')}\nTelegram ID: `{user.id}`\n\n📝 {md_escape(description)}\n\nYou can now chat with this customer through the bot, negotiate the design and set the product price.",parse_mode=ParseMode.MARKDOWN,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Reply to Customer",callback_data=f"adm:stylereply:{request_id}")],[InlineKeyboardButton("💰 Set Product Price",callback_data=f"adm:styleprice:{request_id}")]]))
            for fid in style.get("photos", []): await context.bot.send_photo(chat_id=ADMIN_ID,photo=fid,caption=f"#AD-S{request_id:04d} customer style sample")
            if style.get("video"): await context.bot.send_video(chat_id=ADMIN_ID,video=style["video"],caption=f"#AD-S{request_id:04d} customer style video")
        except Exception: logger.exception("Could not notify admin about style request")
        context.user_data.pop("style_request", None); context.user_data.pop("style_request_step", None)
        await message.reply_text(f"✅ *Style Request #AD-S{request_id:04d} is now open.*\n\nYou can chat with our customer service team here. Explain exactly what you want; we will discuss the design, requirements and price with you.\n\n💬 Send your next message whenever you are ready.",parse_mode=ParseMode.MARKDOWN,reply_markup=style_customer_keyboard(request_id))
        return

    if context.user_data.get("style_payment_phone"):
        context.user_data.pop("style_payment_phone", None); context.user_data["style_phone"] = text
        rid=context.user_data.pop("style_payment_request", None)
        if rid:
            await message.reply_text("✅ Phone number received. Preparing your payment details…")
            await style_create_payment_order(update, context, rid)
        return

    # Custom-style delivery details. The customer chooses the method; the admin only sets the fee.
    if context.user_data.get("style_delivery_step") == "phone":
        context.user_data["style_phone"] = text
        context.user_data["style_delivery_step"] = "destination"
        await message.reply_text("📍 Please send the delivery destination: full address, city/town, state/country (as applicable). This lets our team calculate the correct delivery charge.")
        return
    if context.user_data.get("style_delivery_step") == "destination":
        context.user_data["style_address"] = text
        context.user_data.pop("style_delivery_step", None)
        rid=context.user_data.pop("style_delivery_request", None)
        if rid:
            c=conn(); c.execute("UPDATE style_requests SET quote_status='WAITING_DELIVERY_FEE' WHERE id=?", (rid,)); c.commit(); c.close()
            await message.reply_text("✅ Delivery details received. Our customer service team will now price the delivery and send you the final quote.",reply_markup=style_customer_keyboard(rid))
            await notify_style_admin(context,rid,f"Customer selected delivery and provided destination details: {text[:1500]}\nPlease enter the delivery fee using the admin button.")
        return

    # Customer ↔ admin conversation for an active custom style request.
    active_style = active_style_for_user(update.effective_user.id)
    if active_style and not is_admin(update):
        if context.user_data.get("style_collect_phone"):
            context.user_data.pop("style_collect_phone",None); context.user_data["style_phone"]=text
            await message.reply_text("Thanks. You can continue chatting with customer service.",reply_markup=style_customer_keyboard(active_style["id"])); return
        c=conn(); c.execute("INSERT INTO style_request_messages(request_id,sender_role,sender_id,message) VALUES(?,?,?,?)",(active_style["id"],"customer",update.effective_user.id,text[:2000])); c.commit(); c.close()
        await notify_style_admin(context,active_style["id"],text[:2000])
        await message.reply_text("💬 Message sent to customer service. You can continue explaining what you want here.",reply_markup=style_customer_keyboard(active_style["id"]))
        return

    # Admin reply / quote inputs for a style request.
    sr=context.user_data.get("style_admin_reply")
    if sr and is_admin(update):
        request_id=int(sr); context.user_data.pop("style_admin_reply",None)
        c=conn(); c.execute("INSERT INTO style_request_messages(request_id,sender_role,sender_id,message) VALUES(?,?,?,?)",(request_id,"admin",update.effective_user.id,text[:2000])); c.commit(); c.close()
        c=conn(); row=c.execute("SELECT user_id FROM style_requests WHERE id=?",(request_id,)).fetchone(); c.close()
        if row: await context.bot.send_message(chat_id=row["user_id"],text=f"💬 *AD Fashion Customer Service*\n\n{text[:2000]}",parse_mode=ParseMode.MARKDOWN,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Reply",callback_data=f"style:chat:{request_id}")],[InlineKeyboardButton("🚚 Choose Delivery",callback_data=f"style:delivery:{request_id}")]]))
        await message.reply_text(f"✅ Reply sent to #AD-S{request_id:04d}.")
        return

    if context.user_data.get("style_admin_price") and is_admin(update):
        request_id=int(context.user_data.pop("style_admin_price"))
        try: price=int(''.join(ch for ch in text if ch.isdigit()))
        except ValueError: price=0
        if price<=0: await message.reply_text("Enter a valid product price, e.g. 65000."); return
        c=conn(); c.execute("UPDATE style_requests SET product_price=?, quote_status='WAITING_DELIVERY' WHERE id=?",(price,request_id)); c.commit(); c.close()
        c=conn(); row=c.execute("SELECT user_id FROM style_requests WHERE id=?",(request_id,)).fetchone(); c.close()
        if row: await context.bot.send_message(chat_id=row["user_id"],text=f"💰 *Product price agreed: {money(price)}*\n\nPlease choose your preferred delivery method below. The delivery charge will be added by our customer service team.",parse_mode=ParseMode.MARKDOWN,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚚 Choose Delivery",callback_data=f"style:delivery:{request_id}")]]))
        await message.reply_text(f"✅ Product price for #AD-S{request_id:04d} set to {money(price)}. Customer has been asked to choose delivery.")
        return

    if context.user_data.get("style_admin_delivery_fee") and is_admin(update):
        request_id=int(context.user_data.pop("style_admin_delivery_fee"))
        try: fee=int(''.join(ch for ch in text if ch.isdigit()))
        except ValueError: fee=0
        if fee<0: await message.reply_text("Enter a valid delivery fee."); return
        c=conn(); r=c.execute("SELECT * FROM style_requests WHERE id=?",(request_id,)).fetchone()
        if not r: await message.reply_text("Style request not found."); return
        total=int(r["product_price"] or 0)+fee
        c.execute("UPDATE style_requests SET delivery_fee=?, total_price=?, quote_status='READY_FOR_CONFIRMATION' WHERE id=?",(fee,total,request_id)); c.commit(); c.close()
        await context.bot.send_message(chat_id=r["user_id"],text=f"🧾 *Your Custom Style Quote*\n\nProduct: {money(r['product_price'])}\nDelivery: {STYLE_DELIVERY_LABELS.get(r['delivery_method'],r['delivery_method'])}\nDelivery fee: {money(fee)}\n\n💰 *Total: {money(total)}*\n\nPlease confirm if you want to proceed to payment.",parse_mode=ParseMode.MARKDOWN,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Confirm & Pay",callback_data=f"style:confirmquote:{request_id}")],[InlineKeyboardButton("💬 Chat / Request Change",callback_data=f"style:chat:{request_id}")]]))
        await message.reply_text(f"✅ Delivery fee set for #AD-S{request_id:04d}. Customer can now confirm and pay.")
        return

    if context.user_data.get("support_ticket") == "message":
        context.user_data.pop("support_ticket", None)
        user=update.effective_user
        c=conn(); cur=c.execute("INSERT INTO support_tickets(user_id,username,subject,message) VALUES(?,?,?,?)",(user.id,user.username or '',"Customer Support",text)); ticket_id=cur.lastrowid; c.commit(); c.close()
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"🎧 *Support Ticket #{ticket_id}*\n\nCustomer: {md_escape(user.full_name)}\nUsername: @{md_escape(user.username or 'none')}\nTelegram ID: `{user.id}`\n\n{md_escape(text)}", parse_mode=ParseMode.MARKDOWN)
        except Exception: logger.exception("Could not notify admin about support ticket")
        await message.reply_text(f"✅ Support request #{ticket_id} received. Our team will get back to you.", reply_markup=main_menu())
        return

    state = context.user_data.get("checkout")

    if state == "full_name":
        if not text:
            await message.reply_text("Please send a valid full name.")
            return
        context.user_data["customer_name"] = text
        await ask_phone(update, context)
        return

    if state == "phone":
        if message.contact:
            context.user_data["phone"] = message.contact.phone_number
        else:
            context.user_data["phone"] = text

        method = context.user_data.get("delivery_method", "pickup")
        if method == "pickup":
            await ask_notes(update, context)
        else:
            await ask_address(update, context)
        return

    if state == "address":
        if not text:
            await message.reply_text("Please send a valid delivery address.")
            return

        context.user_data["address"] = text
        await ask_city(update, context)
        return

    if state == "city":
        if not text:
            await message.reply_text("Please send a valid city/town.")
            return

        context.user_data["city"] = text
        method = context.user_data.get("delivery_method")
        if method == "nationwide":
            await ask_state(update, context)
        else:
            context.user_data["state"] = "Bauchi"
            await ask_notes(update, context)
        return

    if state == "state":
        if not text:
            await message.reply_text("Please send a valid state.")
            return

        context.user_data["state"] = text
        await ask_notes(update, context)
        return

    if state == "notes":
        context.user_data["delivery_notes"] = text or "none"
        await show_order_confirmation(update, context)
        return

    if is_admin(update):
        await message.reply_text(
            "👨‍💼 *Admin*\n\n"
            "Type /admin for the admin panel, or use these commands:\n\n"
            "/admin_products\n"
            "/addproduct ID | Name | Category | Price | Description\n"
            "/setphoto PRODUCT_ID\n"
            "/deleteproduct PRODUCT_ID\n"
            "/admin_orders\n"
            "/status ORDER_ID STATUS",
            parse_mode=ParseMode.MARKDOWN,
        )


# -------------------- BUTTONS --------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        data = query.data

        if data == "home":
            welcome_body = get_setting("welcome_message", DEFAULT_WELCOME_MESSAGE)
            await safe_edit_query(query, 
                f"🖤 *{NAME}*\n\n"
                f"*{TAGLINE}*\n\n" + welcome_body,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data == "shop":
            await safe_edit_query(query, 
                "🛍️ *Choose a collection:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data.startswith("cat:"):
            parts = data.split(":", 2)
            category = parts[1]
            subcategory = parts[2] if len(parts) > 2 else None

            if category in SUBCATEGORY_MAP and not subcategory:
                await safe_edit_query(query, 
                    f"*{CATEGORY_LABELS.get(category, category)}*\n\n"
                    "Choose a type:",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=subcategory_menu(category),
                )
            else:
                heading = CATEGORY_LABELS.get(category, category)
                if subcategory:
                    heading += f" — {subcategory_label(category, subcategory)}"
                await safe_edit_query(query, 
                    f"*{heading}*\n\n"
                    "Select a product:",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=category_menu(category, subcategory),
                )

        elif data.startswith("product:"):
            product_id = data.split(":", 1)[1]
            product = get_product(product_id)

            if not product:
                await safe_edit_query(query, 
                    "Product not found.",
                    reply_markup=main_menu(),
                )
                return

            category = (product["category"] or "").strip().lower()
            subcategory = (product["subcategory"] or "").strip().lower()
            back_target = f"cat:{category}:{subcategory}" if subcategory else f"cat:{category}"

            if category in COLOR_FABRIC_CATEGORIES:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎨 Choose Color / Fabric", callback_data=f"colors:{product['id']}")],
                    [InlineKeyboardButton("⬅️ Back", callback_data=back_target)],
                ])
            else:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Add to Cart", callback_data=f"plainqty:{product['id']}")],
                    [InlineKeyboardButton("⚡ Order Now", callback_data=f"plainorder:{product['id']}")],
                    [InlineKeyboardButton("⬅️ Back", callback_data=back_target)],
                ])

            details = [f"*{product['name']}*", "", product["description"] or ""]
            if product["variety"]:
                details += ["", f"🎨 *Variety:* {product['variety']}"]
            if product["features"]:
                details += ["", f"✨ *Features:* {product['features']}"]
            details += ["", f"💰 *{money(product['price'])}*"]
            text = "\n".join(details)

            # Every category can have a product video. Hijabs/Jilbabs then continue
            # into the shared color/fabric photo picker; Textiles/More go directly to quantity.
            sent_media = False
            if product["photo_file_id"]:
                await query.message.reply_photo(
                    photo=product["photo_file_id"],
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
                sent_media = True
            if product["video_file_id"]:
                await query.message.reply_video(
                    video=product["video_file_id"],
                    caption=f"🎥 *{product['name']} — Product Video*\n\n{product['description'] or ''}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard,
                )
                sent_media = True
            elif product["photo_file_id"]:
                # Put the controls on the photo when there is no video.
                # Telegram cannot edit the original text message after it has been replaced.
                await query.message.reply_text(text="Choose an option:", reply_markup=keyboard)
            else:
                await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

            if sent_media:
                try:
                    await query.message.delete()
                except Exception:
                    pass

        elif data.startswith("plainqty:"):
            product_id = data.split(":", 1)[1]
            product = get_product(product_id)
            if not product:
                await safe_edit_query(query, "Product not found.", reply_markup=main_menu())
                return
            category = (product["category"] or "").strip().lower()
            if category in COLOR_FABRIC_CATEGORIES:
                await safe_edit_query(query, "Please choose the color/fabric first.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎨 Choose Color / Fabric", callback_data=f"colors:{product_id}")]]))
                return
            await safe_edit_query(
                query,
                item_selected_text(product, 1),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=item_selected_keyboard(product_id, 1),
            )

        elif data.startswith("plainqtytype:"):
            product_id = data.split(":", 1)[1]
            product = get_product(product_id)
            if not product:
                await safe_edit_query(query, "Product not found.", reply_markup=main_menu())
                return
            context.user_data["shop_qty_target"] = product_id
            await safe_edit_query(
                query,
                f"🔢 *{product['name']}*\n\nHow many would you like? Send a number (e.g. 2, 15, 30), or type *cancel* to go back.",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("plainqtyinc:") or data.startswith("plainqtydec:"):
            parts = data.split(":")
            if len(parts) != 3:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product_id, qty_text = parts[1], parts[2]
            try:
                qty = int(qty_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product = get_product(product_id)
            if not product:
                await safe_edit_query(query, "⚠️ Product is no longer available.", reply_markup=main_menu())
                return
            qty = qty + 1 if data.startswith("plainqtyinc:") else max(1, qty - 1)
            await safe_edit_query(
                query,
                item_selected_text(product, qty),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=item_selected_keyboard(product_id, qty),
            )

        elif data.startswith("plainqtyset:"):
            parts = data.split(":")
            if len(parts) != 3:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product_id, qty_text = parts[1], parts[2]
            try:
                qty = int(qty_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product = get_product(product_id)
            if not product or (product["category"] or "").strip().lower() in COLOR_FABRIC_CATEGORIES or qty < 1:
                await safe_edit_query(query, "⚠️ Product or quantity is no longer available.", reply_markup=main_menu())
                return
            subtotal = (product["price"] or 0) * qty
            text = f"🧾 *Item Selected*\n\nProduct: *{product['name']}*\n🔢 Quantity: *{qty}*\n"
            if product["price"] is not None:
                text += f"💰 Subtotal: *{money(subtotal)}*\n"
            text += "\nWhat would you like to do next?"
            await safe_edit_query(
                query, text, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Add to Cart", callback_data=f"plainadd:{product_id}:{qty}")],
                    [InlineKeyboardButton("⚡ Order Now", callback_data=f"plainordernow:{product_id}:{qty}")],
                    [InlineKeyboardButton("🔢 Change Quantity", callback_data=f"plainqty:{product_id}")],
                    [InlineKeyboardButton("⬅️ Back to Product", callback_data=f"product:{product_id}")],
                ])
            )

        elif data.startswith("plainadd:"):
            parts = data.split(":")
            if len(parts) != 3:
                await safe_edit_query(query, "⚠️ Invalid cart selection.", reply_markup=main_menu())
                return
            product_id = parts[1]
            try:
                qty = int(parts[2])
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid cart selection.", reply_markup=main_menu())
                return
            product = get_product(product_id)
            if not product or (product["category"] or "").strip().lower() in COLOR_FABRIC_CATEGORIES or qty < 1:
                await safe_edit_query(query, "⚠️ Product is no longer available.", reply_markup=main_menu())
                return
            cart = context.user_data.setdefault("cart", {})
            cart[product_id] = cart.get(product_id, 0) + qty
            subtotal = (product["price"] or 0) * qty
            text = f"✅ *Added to Your Cart*\n\n{product['name']} × {qty}\n"
            if product["price"] is not None:
                text += f"💰 Subtotal: *{money(subtotal)}*\n"
            text += "\nWhat would you like to do next?"
            await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 View Cart", callback_data="cart")],
                [InlineKeyboardButton("⚡ Checkout Now", callback_data="checkout")],
                [InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop")],
            ]))

        elif data.startswith("plainorder:"):
            product_id = data.split(":", 1)[1]
            product = get_product(product_id)
            if not product:
                await safe_edit_query(query, "Product not found.", reply_markup=main_menu())
                return
            if (product["category"] or "").strip().lower() in COLOR_FABRIC_CATEGORIES:
                await safe_edit_query(query, "Please choose the color/fabric first.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎨 Choose Color / Fabric", callback_data=f"colors:{product_id}")]]))
                return
            await safe_edit_query(
                query,
                f"🔢 *Choose quantity for {product['name']}*\n\n",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("1", callback_data=f"plainordernow:{product_id}:1"), InlineKeyboardButton("2", callback_data=f"plainordernow:{product_id}:2"), InlineKeyboardButton("3", callback_data=f"plainordernow:{product_id}:3")],
                    [InlineKeyboardButton("4", callback_data=f"plainordernow:{product_id}:4"), InlineKeyboardButton("5", callback_data=f"plainordernow:{product_id}:5"), InlineKeyboardButton("10", callback_data=f"plainordernow:{product_id}:10")],
                    [InlineKeyboardButton("⬅️ Back to Product", callback_data=f"product:{product_id}")],
                ])
            )

        elif data.startswith("plainordernow:"):
            parts = data.split(":")
            if len(parts) != 3:
                await safe_edit_query(query, "⚠️ Invalid order selection.", reply_markup=main_menu())
                return
            product_id = parts[1]
            try:
                qty = int(parts[2])
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid order selection.", reply_markup=main_menu())
                return
            product = get_product(product_id)
            if not product or (product["category"] or "").strip().lower() in COLOR_FABRIC_CATEGORIES or qty < 1:
                await safe_edit_query(query, "⚠️ Product is no longer available.", reply_markup=main_menu())
                return
            cart = context.user_data.setdefault("cart", {})
            cart[product_id] = cart.get(product_id, 0) + qty
            await checkout(update, context)

        elif data.startswith("colors:"):
            product_id = data.split(":", 1)[1]
            product = get_product(product_id)
            if not product:
                await safe_edit_query(query, "Product not found.", reply_markup=main_menu())
                return
            if (product["category"] or "").strip().lower() not in COLOR_FABRIC_CATEGORIES:
                await safe_edit_query(
                    query,
                    "🖼️ This product uses its own product photo. Color/fabric selection is available only for Hijabs and Jilbabs.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Product", callback_data=f"product:{product_id}")]])
                )
                return
            colors = get_color_options()
            if not colors:
                await safe_edit_query(query, 
                    "🎨 No color/fabric photos are available yet. Please contact us or try again later.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Product", callback_data=f"product:{product_id}")],[InlineKeyboardButton("🏠 Main Menu", callback_data="home")]])
                )
                return
            await safe_edit_query(query, 
                f"🎨 *Choose the color/fabric you want for {product['name']}.*\n\n"
                "Tap the button under the actual fabric photo. Your selected photo will be attached to the order for our team.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Product", callback_data=f"product:{product_id}")]])
            )
            for color in colors:
                try:
                    await context.bot.send_photo(
                        chat_id=update.effective_user.id,
                        photo=color["photo_file_id"],
                        caption=f"🎨 Fabric/Color Option #{color['id']}",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Choose this color", callback_data=f"colorpick:{product_id}:{color['id']}")]])
                    )
                except Exception:
                    logger.exception("Could not send color option %s", color["id"])
            return

        elif data.startswith("colorpick:"):
            parts = data.split(":")
            if len(parts) != 3:
                await safe_edit_query(query, "⚠️ Invalid color selection.", reply_markup=main_menu())
                return
            product_id, color_id_text = parts[1], parts[2]
            product = get_product(product_id)
            if product and (product["category"] or "").strip().lower() not in COLOR_FABRIC_CATEGORIES:
                await safe_edit_query(query, "⚠️ Color/fabric selection is available only for Hijabs and Jilbabs.", reply_markup=main_menu())
                return
            try:
                color_id = int(color_id_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid color selection.", reply_markup=main_menu())
                return
            color = get_color_option(color_id)
            if not product or not color:
                await safe_edit_query(query, "⚠️ That product or color is no longer available.", reply_markup=main_menu())
                return
            context.user_data["selected_product"] = product_id
            context.user_data["selected_color"] = color_id

            await safe_edit_query(query, 
                item_selected_text(product, 1, color_id=color_id),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=item_selected_keyboard(product_id, 1, color_id=color_id),
            )

        elif data.startswith("qty:"):
            parts = data.split(":")
            if len(parts) != 4:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product_id, color_id_text, qty_text = parts[1], parts[2], parts[3]
            try:
                color_id, qty = int(color_id_text), int(qty_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product, color = get_product(product_id), get_color_option(color_id)
            if not product or not color or qty < 1:
                await safe_edit_query(query, "⚠️ Product or color is no longer available.", reply_markup=main_menu())
                return
            # Quantity is a selection step, not an automatic cart action.
            # This gives the customer an explicit choice between adding the item
            # to the cart or ordering it immediately.
            context.user_data["pending_cart_item"] = {
                "product_id": product_id,
                "color_id": color_id,
                "qty": qty,
            }
            context.user_data["selected_product"] = product_id
            context.user_data["selected_color"] = color_id

            subtotal = (product["price"] or 0) * qty
            summary = (
                "🧾 *Item Selected*\n\n"
                f"Product: *{product['name']}*\n"
                f"🎨 Fabric/Color: Photo #{color_id}\n"
                f"🔢 Quantity: *{qty}*\n"
            )
            if product["price"] is not None:
                summary += f"💰 Subtotal: *{money(subtotal)}*\n"
            summary += "\nWhat would you like to do next?"

            await safe_edit_query(query, 
                summary,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Add to Cart", callback_data=f"addcart:{product_id}:{color_id}:{qty}")],
                    [InlineKeyboardButton("⚡ Order Now", callback_data=f"ordernow:{product_id}:{color_id}:{qty}")],
                    [InlineKeyboardButton("🔢 Change Quantity", callback_data=f"qtychoose:{product_id}:{color_id}")],
                    [InlineKeyboardButton("🎨 Change Color", callback_data=f"colors:{product_id}")],
                ])
            )

        elif data.startswith("addcart:"):
            parts = data.split(":")
            if len(parts) != 4:
                await safe_edit_query(query, "⚠️ Invalid cart selection.", reply_markup=main_menu())
                return
            product_id, color_id_text, qty_text = parts[1], parts[2], parts[3]
            try:
                color_id, qty = int(color_id_text), int(qty_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid cart selection.", reply_markup=main_menu())
                return
            product, color = get_product(product_id), get_color_option(color_id)
            if not product or not color or qty < 1:
                await safe_edit_query(query, "⚠️ Product or fabric/color is no longer available.", reply_markup=main_menu())
                return

            key = cart_key(product_id, color_id)
            cart = context.user_data.setdefault("cart", {})
            cart[key] = cart.get(key, 0) + qty
            context.user_data.pop("pending_cart_item", None)
            context.user_data.pop("selected_product", None)
            context.user_data.pop("selected_color", None)

            subtotal = (product["price"] or 0) * qty
            text = (
                "✅ *Added to Your Cart*\n\n"
                f"{product['name']} × {qty}\n"
                f"🎨 Fabric/Color: Photo #{color_id}\n"
            )
            if product["price"] is not None:
                text += f"💰 Subtotal: *{money(subtotal)}*\n"
            text += "\nWhat would you like to do next?"

            await safe_edit_query(query, 
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 View Cart", callback_data="cart")],
                    [InlineKeyboardButton("⚡ Checkout Now", callback_data="checkout")],
                    [InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop")],
                ])
            )

        elif data.startswith("ordernow:"):
            parts = data.split(":")
            if len(parts) != 4:
                await safe_edit_query(query, "⚠️ Invalid order selection.", reply_markup=main_menu())
                return
            product_id, color_id_text, qty_text = parts[1], parts[2], parts[3]
            try:
                color_id, qty = int(color_id_text), int(qty_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid order selection.", reply_markup=main_menu())
                return
            product, color = get_product(product_id), get_color_option(color_id)
            if not product or not color or qty < 1:
                await safe_edit_query(query, "⚠️ Product or fabric/color is no longer available.", reply_markup=main_menu())
                return

            key = cart_key(product_id, color_id)
            cart = context.user_data.setdefault("cart", {})
            cart[key] = cart.get(key, 0) + qty
            context.user_data.pop("pending_cart_item", None)
            context.user_data.pop("selected_product", None)
            context.user_data.pop("selected_color", None)
            await checkout(update, context)

        elif data.startswith("qtychoose:"):
            parts = data.split(":")
            if len(parts) != 3:
                await safe_edit_query(query, "⚠️ Invalid quantity selection.", reply_markup=main_menu())
                return
            product_id, color_id_text = parts[1], parts[2]
            try:
                color_id = int(color_id_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid quantity selection.", reply_markup=main_menu())
                return
            product, color = get_product(product_id), get_color_option(color_id)
            if not product or not color:
                await safe_edit_query(query, "⚠️ Product or fabric/color is no longer available.", reply_markup=main_menu())
                return

            await safe_edit_query(query, 
                f"🔢 *Choose Quantity*\n\nProduct: *{product['name']}*\n🎨 Fabric/Color: Photo #{color_id}\n\nHow many do you want?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("1", callback_data=f"qty:{product_id}:{color_id}:1"), InlineKeyboardButton("2", callback_data=f"qty:{product_id}:{color_id}:2"), InlineKeyboardButton("3", callback_data=f"qty:{product_id}:{color_id}:3")],
                    [InlineKeyboardButton("4", callback_data=f"qty:{product_id}:{color_id}:4"), InlineKeyboardButton("5", callback_data=f"qty:{product_id}:{color_id}:5"), InlineKeyboardButton("10", callback_data=f"qty:{product_id}:{color_id}:10")],
                    [InlineKeyboardButton("🔢 Enter Custom Quantity", callback_data=f"qtytype:{product_id}:{color_id}")],
                    [InlineKeyboardButton("🎨 Change Color", callback_data=f"colors:{product_id}")],
                ])
            )

        elif data.startswith("qtytype:"):
            parts = data.split(":")
            if len(parts) != 3:
                await safe_edit_query(query, "⚠️ Invalid selection.", reply_markup=main_menu())
                return
            product_id, color_id_text = parts[1], parts[2]
            try:
                color_id = int(color_id_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid selection.", reply_markup=main_menu())
                return
            product, color = get_product(product_id), get_color_option(color_id)
            if not product or not color:
                await safe_edit_query(query, "⚠️ Product or fabric/color is no longer available.", reply_markup=main_menu())
                return
            context.user_data["shop_qty_color_target"] = {"product_id": product_id, "color_id": color_id}
            await safe_edit_query(query, 
                f"🔢 *{product['name']}* — Fabric/Color #{color_id}\n\nHow many would you like? Send a number (e.g. 2, 15, 30), or type *cancel* to go back.",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("qtyinc:") or data.startswith("qtydec:"):
            parts = data.split(":")
            if len(parts) != 4:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product_id, color_id_text, qty_text = parts[1], parts[2], parts[3]
            try:
                color_id, qty = int(color_id_text), int(qty_text)
            except ValueError:
                await safe_edit_query(query, "⚠️ Invalid quantity.", reply_markup=main_menu())
                return
            product, color = get_product(product_id), get_color_option(color_id)
            if not product or not color:
                await safe_edit_query(query, "⚠️ Product or fabric/color is no longer available.", reply_markup=main_menu())
                return
            qty = qty + 1 if data.startswith("qtyinc:") else max(1, qty - 1)
            await safe_edit_query(query, 
                item_selected_text(product, qty, color_id=color_id),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=item_selected_keyboard(product_id, qty, color_id=color_id),
            )

        elif data.startswith("add:"):
            # Backward compatibility for old buttons/old sessions: use the first color if available.
            parts = data.split(":")
            product_id = parts[1]
            order_now = len(parts) > 2 and parts[2] == "now"
            colors = get_color_options()
            product = get_product(product_id)
            if product and (product["category"] or "").strip().lower() in COLOR_FABRIC_CATEGORIES and colors:
                await safe_edit_query(query, "Please choose the color/fabric photo first.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎨 Choose Color / Fabric", callback_data=f"colors:{product_id}")]]))
                return
            context.user_data.setdefault("cart", {})
            cart = context.user_data["cart"]
            cart[product_id] = cart.get(product_id, 0) + 1
            if order_now:
                await checkout(update, context)
            else:
                await safe_edit_query(query, "✅ *Added to your cart.*", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 View Cart", callback_data="cart")],[InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop")]]))

        elif data == "cart":
            await show_cart(update, context)

        elif data == "noop":
            pass

        elif data.startswith("cartsetqty:"):
            key = data.split(":", 1)[1]
            cart = context.user_data.get("cart", {})
            if key not in cart:
                await show_cart(update, context)
            else:
                product, _ = cart_product_color(key)
                name = product["name"] if product else "this item"
                context.user_data["cart_qty_target"] = key
                await safe_edit_query(query,
                    f"🔢 *Set Quantity*\n\n"
                    f"How many of *{md_escape(name)}* would you like? "
                    "Send a number (e.g. 2, 15, 30), or type *cancel* to go back.",
                    parse_mode=ParseMode.MARKDOWN,
                )

        elif data.startswith("cartinc:"):
            key = data.split(":", 1)[1]
            cart = context.user_data.get("cart", {})
            if key in cart:
                cart[key] += 1
                context.user_data["cart"] = cart
            await show_cart(update, context)

        elif data.startswith("cartdec:"):
            key = data.split(":", 1)[1]
            cart = context.user_data.get("cart", {})
            if key in cart:
                cart[key] -= 1
                if cart[key] <= 0:
                    cart.pop(key, None)
                context.user_data["cart"] = cart
            await show_cart(update, context)

        elif data.startswith("cartdel:"):
            key = data.split(":", 1)[1]
            cart = context.user_data.get("cart", {})
            cart.pop(key, None)
            context.user_data["cart"] = cart
            await show_cart(update, context)

        elif data == "checkout":
            await checkout(update, context)

        elif data == "clear":
            context.user_data["cart"] = {}
            await safe_edit_query(query, 
                "🛒 Your cart is empty.",
                reply_markup=main_menu(),
            )

        elif data == "confirm_order":
            await create_order(update, context)

        elif data == "cancel_order":
            context.user_data.clear()
            await safe_edit_query(query, 
                "❌ *Order cancelled.*\n\n"
                "Your cart has been cleared.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data.startswith("payment_done:"):
            await payment_done(update, context, data.split(":", 1)[1])

        elif data.startswith("delivery:"):
            method = data.split(":", 1)[1]
            if method not in DELIVERY_LABELS:
                await safe_edit_query(query, "⚠️ Unknown delivery method.", reply_markup=main_menu())
                return
            await safe_edit_query(query, 
                f"🚚 *{DELIVERY_LABELS[method]}* selected.",
                parse_mode=ParseMode.MARKDOWN,
            )
            await start_delivery_details(update, context, method)

        elif data == "help":
            await help_cmd(update, context)

        elif data == "style":
            context.user_data["style_request"] = {"photos": [], "video": None}
            await safe_edit_query(query,
                "✨ *Got your own style or fabric?*\n\n"
                "We'd love to help bring it to life! Send us a photo (or a few) — "
                "and a video too, if you have one — showing what you have in mind.\n\n"
                "When you're done, tap *✅ I'm Done Sending* and tell us a bit about what "
                "you want (color, size, style). Our team will chat with you right here "
                "to work out the details and price together.\n\n"
                "Prefer WhatsApp? Tap the button below instead — either way works! 💛",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ I'm Done Sending", callback_data="style:done")],
                    [InlineKeyboardButton("💬 Chat on WhatsApp Instead", url=f"https://wa.me/{WHATSAPP}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="style:cancel")],
                ])
            )

        elif data == "style:done":
            style = context.user_data.get("style_request")
            if not style or (not style.get("photos") and not style.get("video")):
                await safe_edit_query(query, 
                    "You haven't sent any photos or video yet.\n\n"
                    "Send at least one, or chat with us on WhatsApp instead.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Chat on WhatsApp Instead", url=f"https://wa.me/{WHATSAPP}")],
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="home")],
                    ]),
                )
                return
            context.user_data["style_request_step"] = "description"
            await safe_edit_query(query, 
                "📝 *Almost there!*\n\n"
                "Tell us what you'd like in your own words — color, size, fabric, occasion, anything that helps.\n\n"
                "Example: _I want this style in navy blue, size 42._",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data == "style:cancel":
            context.user_data.pop("style_request", None)
            context.user_data.pop("style_request_step", None)
            await safe_edit_query(query, "❌ Style request cancelled.", reply_markup=main_menu())

        elif data.startswith("style:delivery:"):
            request_id=int(data.split(":")[2]); await send_style_delivery_menu(update.effective_message,request_id)
        elif data.startswith("style:setdelivery:"):
            _,_,rid,method=data.split(":",3); rid=int(rid)
            c=conn(); r=c.execute("SELECT * FROM style_requests WHERE id=? AND user_id=?",(rid,update.effective_user.id)).fetchone(); c.close()
            if not r:
                await safe_edit_query(query, "⚠️ Request not found.")
                return
            c=conn(); c.execute("UPDATE style_requests SET delivery_method=?,quote_status='WAITING_DELIVERY_FEE' WHERE id=?",(method,rid)); c.commit(); c.close()
            await safe_edit_query(query,f"🚚 *{STYLE_DELIVERY_LABELS.get(method,method)}* selected.",parse_mode=ParseMode.MARKDOWN)
            if method == "pickup":
                await query.message.reply_text("📍 Pickup selected: Bauchi Central Market. Our team will now set the delivery charge (normally ₦0 for pickup) and send your final quote.")
                await notify_style_admin(context,rid,f"Customer selected {STYLE_DELIVERY_LABELS.get(method,method)}. Please set the delivery fee (enter 0 if there is no fee).")
            else:
                context.user_data["style_delivery_request"]=rid; context.user_data["style_delivery_step"]="phone"
                await query.message.reply_text("📞 Please send the phone number for this delivery. Then I will ask for the delivery destination.")
        elif data.startswith("style:chat:"):
            rid=int(data.split(":")[2]); context.user_data["style_chat_request"]=rid
            await query.message.reply_text(f"💬 Continue your conversation for #AD-S{rid:04d}. Send your message here and our customer service team will receive it.")
        elif data.startswith("style:confirmquote:"):
            rid=int(data.split(":")[2]); c=conn(); r=c.execute("SELECT * FROM style_requests WHERE id=? AND user_id=?",(rid,update.effective_user.id)).fetchone(); c.close()
            if not r or r["quote_status"]!="READY_FOR_CONFIRMATION":
                await safe_edit_query(query, "⚠️ Quote is not ready yet.")
                return
            c=conn(); c.execute("UPDATE style_requests SET quote_status='CONFIRMED' WHERE id=?",(rid,)); c.commit(); c.close()
            if not context.user_data.get("style_phone"):
                context.user_data["style_payment_phone"]=True; context.user_data["style_payment_request"]=rid
                await query.message.reply_text("✅ Quote confirmed!\n\n📞 Before payment, please send your phone number for the order.")
            else:
                await style_create_payment_order(update,context,rid)
        elif data.startswith("style:close:"):
            rid=int(data.split(":")[2]); c=conn(); c.execute("UPDATE style_requests SET status='CLOSED',quote_status='CLOSED' WHERE id=? AND user_id=?",(rid,update.effective_user.id)); c.commit(); c.close(); await safe_edit_query(query,"❌ Style request closed.",reply_markup=main_menu())
        elif data == "support":
            await support_cmd(update, context)

        elif data == "support_cancel":
            context.user_data.pop("support_ticket", None)
            await safe_edit_query(query, "Support request cancelled.", reply_markup=main_menu())

        elif data == "referral":
            user=update.effective_user; upsert_customer(user, source="referral")
            c=conn(); referral_count=c.execute("SELECT COUNT(*) FROM customers WHERE referrer_id=?",(user.id,)).fetchone()[0]; c.close()
            await safe_edit_query(query, 
                "🎁 *Refer a Friend*\n\n"
                f"Share this link with a friend:\n\n{referral_link(context,user.id)}\n\n"
                f"👥 Referrals recorded: {referral_count}\n\n"
                "When they start the bot through your link, the referral is recorded. 🖤✨",
                parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

        elif data.startswith("review:"):
            _, order_id_text, rating_text=data.split(":",2)
            try: order_id=int(order_id_text); rating=int(rating_text)
            except ValueError: await safe_edit_query(query, "Invalid review.", reply_markup=main_menu()); return
            c=conn(); row=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,update.effective_user.id)).fetchone()
            if not row:
                c.close(); await safe_edit_query(query, "Order not found.", reply_markup=main_menu()); return
            c.execute("INSERT INTO reviews(order_id,user_id,rating,comment) VALUES(?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET rating=excluded.rating",(order_id,update.effective_user.id,rating,"")); c.commit(); c.close()
            context.user_data["review_order"]=order_id
            await safe_edit_query(query, f"⭐ Thank you for rating Order #AD-{order_id:05d} {rating}/5.\n\nYou may now send a short comment, or type *skip*.", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

        elif data.startswith("reorder:"):
            try: order_id=int(data.split(":",1)[1])
            except ValueError: await safe_edit_query(query, "Invalid order.", reply_markup=main_menu()); return
            c=conn(); row=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,update.effective_user.id)).fetchone(); c.close()
            if not row or not row["product_ids"]:
                await safe_edit_query(query, "Some products from this order are no longer available. Please shop again.", reply_markup=main_menu()); return
            cart=context.user_data.setdefault("cart", {})
            added=0
            for key in row["product_ids"].split(","):
                pid, color_id = split_cart_key(key)
                if get_product(pid):
                    new_key = cart_key(pid, color_id) if color_id is not None and get_color_option(color_id) else pid
                    cart[new_key] = cart.get(new_key,0)+1
                    added += 1
            await safe_edit_query(query, f"🛒 Added {added} item(s) from Order #AD-{order_id:05d} to your cart.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 View Cart", callback_data="cart")],[InlineKeyboardButton("🛍️ Shop More", callback_data="shop")]]))

        elif data.startswith("delivery_confirm:"):
            parts = data.split(":")
            try: order_id=int(parts[1])
            except ValueError: await safe_edit_query(query, "Invalid order.", reply_markup=main_menu()); return
            outcome = parts[2] if len(parts) > 2 else "yes"
            c=conn(); row=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,update.effective_user.id)).fetchone(); c.close()
            if not row:
                await safe_edit_query(query, "Order not found.", reply_markup=main_menu())
                return
            if outcome == "yes":
                await update_order_status(context, order_id, "DELIVERED")
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"✅ Customer confirmed receipt of Order #AD-{order_id:05d}. Marked as DELIVERED.",
                    )
                except Exception:
                    logger.exception("Could not notify admin of delivery confirmation")
                await safe_edit_query(query, "✅ Thanks for confirming! We hope you love it. 💛", reply_markup=main_menu())
            else:
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"🚨 *Delivery issue — Order #AD-{order_id:05d}*\n\nCustomer says they have NOT received this order, despite it being marked out for delivery. Please follow up urgently.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    logger.exception("Could not notify admin of delivery non-receipt")
                await safe_edit_query(query, "❌ Thanks for letting us know. Our team will follow up with you shortly.", reply_markup=main_menu())

        elif data == "orders":
            await orders_cmd(update, context)

        elif data == "contact":
            await safe_edit_query(query, 
                f"📞 *{NAME}*\n\n"
                f"{PHONE1}\n"
                f"{PHONE2}\n\n"
                f"WhatsApp: https://wa.me/{WHATSAPP}\n"
                f"Website: {WEBSITE}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data == "location":
            await safe_edit_query(query, 
                f"📍 *Our Location*\n\n{ADDRESS}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data.startswith("adm:"):
            if not is_admin(update):
                return
            await handle_admin_callback(update, context, data)
    except Exception:
        logger.exception("Unhandled error in buttons() while processing callback_data=%r", query.data)
        try:
            await safe_edit_query(
                query,
                "⚠️ Something went wrong loading that. Please try again, or use /admin for the admin panel.",
                reply_markup=main_menu(),
            )
        except Exception:
            logger.exception("Could not even show the fallback error message to the user.")


# -------------------- ADMIN --------------------

async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    rows = get_products()

    if not rows:
        await update.message.reply_text("No products.")
        return

    text = "🛠️ *Products*\n\n"

    for row in rows:
        text += (
            f"`{row['id']}` | {row['name']} | "
            f"{row['category']} | {money(row['price'])}\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
    )


async def addproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    raw = update.message.text.partition(" ")[2]
    parts = [x.strip() for x in raw.split("|")]

    if len(parts) < 5:
        await update.message.reply_text(
            "Format:\n"
            "/addproduct ID | Name | Category | Price | Description\n\n"
            "Use 0 for price-on-request."
        )
        return

    product_id, name, category, price, description = parts[:5]

    try:
        price_value = (
            None
            if price == "0"
            else int(
                price.replace(",", "")
                .replace("₦", "")
                .strip()
            )
        )
    except ValueError:
        await update.message.reply_text(
            "Invalid price. Enter a number, e.g. 24000, or 0."
        )
        return

    c = conn()
    c.execute(
        """
        INSERT OR REPLACE INTO products
        (id, name, category, price, description, active)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            product_id,
            name,
            category.lower(),
            price_value,
            description,
        ),
    )
    c.commit()
    c.close()

    await update.message.reply_text(
        f"✅ Saved: {name}"
    )


async def deleteproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    product_id = update.message.text.partition(" ")[2].strip()

    if not product_id:
        await update.message.reply_text(
            "Use: /deleteproduct PRODUCT_ID"
        )
        return

    c = conn()
    c.execute(
        "UPDATE products SET active=0 WHERE id=?",
        (product_id,),
    )
    c.commit()
    c.close()

    await update.message.reply_text(
        "✅ Product removed from the shop."
    )


async def setvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    product_id = update.message.text.partition(" ")[2].strip()
    if not product_id or not get_product(product_id):
        await update.message.reply_text("Use: /setvideo PRODUCT_ID")
        return
    context.user_data["video_target"] = product_id
    await update.message.reply_text(f"🎥 Now send the product video for `{product_id}`.", parse_mode=ParseMode.MARKDOWN)


async def setphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    product_id = update.message.text.partition(" ")[2].strip()

    if not product_id:
        await update.message.reply_text(
            "Use: /setphoto PRODUCT_ID"
        )
        return

    if not get_product(product_id):
        await update.message.reply_text(
            "Product ID not found."
        )
        return

    context.user_data["photo_target"] = product_id

    await update.message.reply_text(
        f"📸 Now send the photo for `{product_id}`.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin: save product video during guided add flow.
    if is_admin(update):
        admin_add = context.user_data.get("admin_add")
        if admin_add and admin_add.get("step") == "video":
            file_id = update.message.video.file_id
            c = conn()
            c.execute("UPDATE products SET video_file_id=? WHERE id=?", (file_id, admin_add["data"]["id"]))
            c.commit(); c.close()
            await finalize_admin_add(update.message, context, admin_add["data"])
            return
        product_id = context.user_data.get("video_target")
        if product_id:
            c = conn(); c.execute("UPDATE products SET video_file_id=? WHERE id=?", (update.message.video.file_id, product_id)); c.commit(); c.close()
            context.user_data.pop("video_target", None)
            await update.message.reply_text("✅ Product video saved.", reply_markup=admin_back_button())
            return

    # Customer: collect a style/inspiration video. During an active chat, forward it to admin too.
    active_style = active_style_for_user(update.effective_user.id) if not is_admin(update) else None
    if active_style and not context.user_data.get("style_request"):
        fid=update.message.video.file_id
        c=conn(); c.execute("INSERT INTO style_request_media(request_id,media_type,file_id) VALUES(?,?,?)",(active_style["id"],"video",fid)); c.execute("INSERT INTO style_request_messages(request_id,sender_role,sender_id,message) VALUES(?,?,?,?)",(active_style["id"],"customer",update.effective_user.id,"[Customer sent a video]") ); c.commit(); c.close()
        await context.bot.send_video(chat_id=ADMIN_ID,video=fid,caption=f"#AD-S{active_style['id']:04d} customer message")
        await notify_style_admin(context,active_style["id"],"[Customer sent a video]")
        return
    # Customer: collect a style/inspiration video.
    style = context.user_data.get("style_request")
    if style is not None:
        style["video"] = update.message.video.file_id
        await update.message.reply_text(
            "🎥 Sample video received. Send another photo/video, or tap Done when finished.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data="style:done")]])
        )
        return

    if is_admin(update):
        await update.message.reply_text("Use the product video upload option from Admin → Products → Edit Product.")


async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin flow: setting the /start welcome photo.
    if is_admin(update) and context.user_data.get("admin_edit_welcome_photo"):
        context.user_data.pop("admin_edit_welcome_photo", None)
        file_id = update.message.photo[-1].file_id
        set_setting("welcome_photo_file_id", file_id)
        await update.message.reply_text("✅ Welcome photo updated.", reply_markup=admin_back_button())
        return

    # Customer flow: collect a style/inspiration photo or send one during an active chat.
    active_style = active_style_for_user(update.effective_user.id) if not is_admin(update) else None
    style = context.user_data.get("style_request")
    if active_style and style is None:
        fid=update.message.photo[-1].file_id
        c=conn(); c.execute("INSERT INTO style_request_media(request_id,media_type,file_id) VALUES(?,?,?)",(active_style["id"],"photo",fid)); c.execute("INSERT INTO style_request_messages(request_id,sender_role,sender_id,message) VALUES(?,?,?,?)",(active_style["id"],"customer",update.effective_user.id,"[Customer sent a photo]") ); c.commit(); c.close()
        await context.bot.send_photo(chat_id=ADMIN_ID,photo=fid,caption=f"#AD-S{active_style['id']:04d} customer message")
        await notify_style_admin(context,active_style["id"],"[Customer sent a photo]")
        return
    if style is not None and not is_admin(update):
        style.setdefault("photos", []).append(update.message.photo[-1].file_id)
        await update.message.reply_text(
            "📸 Sample photo received. Send another photo/video, or tap Done when finished.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data="style:done")]])
        )
        return

    # Admin flow: add a general fabric/color photo to the shared library.
    if is_admin(update) and context.user_data.get("admin_color_add"):
        file_id = update.message.photo[-1].file_id
        c = conn()
        cur = c.execute("INSERT INTO color_options(photo_file_id, active) VALUES(?,1)", (file_id,))
        color_id = cur.lastrowid
        c.commit(); c.close()
        # Keep the upload mode active so the admin can send several photos in a row
        # (including a Telegram photo album) without naming them one by one.
        await update.message.reply_text(
            f"✅ Fabric/color photo #{color_id} added.\n\n"
            "You can send more fabric/color photos now. When you are finished, tap Done.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="adm:colorsdone")],
                [InlineKeyboardButton("🎨 Manage Color/Fabric Photos", callback_data="adm:colors")],
            ])
        )
        return

    # Admin flow: setting a product photo via /setphoto.
    if is_admin(update) and context.user_data.get("photo_target"):
        product_id = context.user_data["photo_target"]
        file_id = update.message.photo[-1].file_id

        c = conn()
        c.execute(
            "UPDATE products SET photo_file_id=? WHERE id=?",
            (file_id, product_id),
        )
        c.commit()
        c.close()

        context.user_data.pop("photo_target", None)

        await update.message.reply_text(
            "✅ Product photo saved."
        )
        return

    # Admin guided add-product flow: save the uploaded photo as the main/first product image.
    admin_add = context.user_data.get("admin_add")
    if is_admin(update) and admin_add and admin_add.get("step") == "photo":
        data = admin_add["data"]
        file_id = update.message.photo[-1].file_id
        c = conn()
        c.execute(
            """INSERT OR REPLACE INTO products
               (id, name, category, subcategory, price, description, variety, features, photo_file_id, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (data["id"], data["name"], data["category"], data.get("subcategory", ""), data["price"], data["description"], data.get("variety", ""), data.get("features", ""), file_id),
        )
        c.commit(); c.close()
        admin_add["step"] = "video"
        await update.message.reply_text(
            f"📸 Main product photo saved for *{data['name']}*.\n\n"
            "🎥 Now send the product video, or type *skip* if you do not want a video.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Customer flow: uploading a payment receipt screenshot.
    order_id = context.user_data.get("awaiting_receipt")

    if order_id:
        file_id = update.message.photo[-1].file_id

        c = conn()
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

        if not row or row["user_id"] != update.effective_user.id:
            c.close()
            context.user_data.pop("awaiting_receipt", None)
            await update.message.reply_text(
                "That order could not be matched to you. Please contact us directly.",
                reply_markup=main_menu(),
            )
            return

        c.execute(
            "UPDATE orders SET receipt_file_id=? WHERE id=?",
            (file_id, order_id),
        )
        c.commit()
        c.close()

        order_number = f"AD-{order_id:05d}"
        context.user_data.pop("awaiting_receipt", None)

        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=file_id,
                caption=(
                    f"🧾 *Receipt received — Order #{order_number}*\n"
                    f"Customer: {md_escape(row['customer_name'])}\n"
                    f"Total: {money(row['total'])}\n\n"
                    "Verify against your bank record, then use "
                    f"/status {order_id} PAID"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            logger.exception("Could not forward receipt photo to admin.")

        await update.message.reply_text(
            f"✅ Receipt received for #{order_number}. "
            "We'll confirm your order shortly.",
            reply_markup=main_menu(),
        )
        return

    if is_admin(update):
        await update.message.reply_text(
            "Use /setphoto PRODUCT_ID first."
        )


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    c = conn()
    rows = c.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT 20"
    ).fetchall()
    c.close()

    if not rows:
        await update.message.reply_text("No orders yet.")
        return

    for row in rows:
        order_number = f"AD-{row['id']:05d}"

        if row["payment_notified"]:
            payment_line = "💳 Payment notified" + (
                " — 🧾 receipt attached" if row["receipt_file_id"] else " — ⚠️ no receipt sent"
            )
        else:
            payment_line = "⏳ No payment notification yet"

        method = row["delivery_method"] or "pickup"
        delivery_line = f"🚚 {DELIVERY_LABELS.get(method, method)}"
        if method != "pickup" and row["city"]:
            delivery_line += f" — {md_escape(row['city'])}" + (f", {md_escape(row['state'])}" if row["state"] else "")

        text = (
            f"📦 *Order #{order_number}*\n"
            f"Customer: {md_escape(row['customer_name'])}\n"
            f"Phone: {md_escape(row['phone'])}\n"
            f"Address: {md_escape(row['address'])}\n"
            f"{delivery_line}\n\n"
            f"Items:\n{md_escape(row['items'])}\n\n"
            f"Total: {money(row['total'])}\n"
            f"Status: *{fmt_status(row['status'])}*\n"
            f"{payment_line}\n"
            f"Created: {row['created_at']}"
        )

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
        )

        if row["receipt_file_id"]:
            try:
                await update.message.reply_photo(
                    photo=row["receipt_file_id"],
                    caption=f"🧾 Receipt for #{order_number}",
                )
            except Exception:
                logger.exception("Could not resend stored receipt photo.")


ALLOWED_STATUSES = {
    "PENDING",
    "AWAITING_PAYMENT",
    "PAID",
    "PROCESSING",
    "OUT_FOR_DELIVERY",
    "DELIVERY_FAILED",
    "DELIVERED",
    "CANCELLED",
}


async def update_order_status(context: ContextTypes.DEFAULT_TYPE, order_id: int, new_status: str):
    """Updates an order's status, notifies the customer, and returns the row (or None)."""
    c = conn()
    row = c.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
    ).fetchone()

    if not row:
        c.close()
        return None

    if new_status == "DELIVERY_FAILED":
        c.execute("UPDATE orders SET status=?, delivery_attempts=delivery_attempts+1, delivery_updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, order_id))
    elif new_status in {"OUT_FOR_DELIVERY", "DELIVERED"}:
        c.execute("UPDATE orders SET status=?, delivery_updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, order_id))
    else:
        c.execute("UPDATE orders SET status=? WHERE id=?", (new_status, order_id))
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"

    await send_order_status_notification(context, row["user_id"], order_number, new_status)
    return row


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    raw = update.message.text.partition(" ")[2].strip()
    parts = raw.split(maxsplit=1)

    if len(parts) != 2:
        await update.message.reply_text(
            "Format:\n/status ORDER_ID STATUS\n\n"
            "Example:\n/status 1 PAID"
        )
        return

    try:
        order_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("Invalid order ID.")
        return

    new_status = parts[1].strip().upper()

    if new_status not in ALLOWED_STATUSES:
        await update.message.reply_text(
            "Allowed statuses:\n" + "\n".join(sorted(ALLOWED_STATUSES))
        )
        return

    row = await update_order_status(context, order_id, new_status)

    if not row:
        await update.message.reply_text("Order not found.")
        return

    order_number = f"AD-{order_id:05d}"

    await update.message.reply_text(
        f"✅ Order #{order_number} updated to *{fmt_status(new_status)}*.",
        parse_mode=ParseMode.MARKDOWN,
    )


# -------------------- ADMIN PANEL (PHASE 5) --------------------

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="adm:products")],
        [InlineKeyboardButton("📋 View Orders", callback_data="adm:vieworders")],
        [InlineKeyboardButton("📊 Sales Summary", callback_data="adm:sales")],
        [InlineKeyboardButton("📈 Weekly Business Review", callback_data="adm:weekly")],
        [InlineKeyboardButton("🚚 Delivery", callback_data="adm:delivery")],
        [InlineKeyboardButton("🎧 Support Tickets", callback_data="adm:support")],
        [InlineKeyboardButton("⭐ Customer Reviews", callback_data="adm:reviews")],
        [InlineKeyboardButton("💳 Payments", callback_data="adm:payments")],
        [InlineKeyboardButton("📣 Marketing & Launch", callback_data="adm:marketing")],
        [InlineKeyboardButton("🎨 Color/Fabric Photos", callback_data="adm:colors")],
        [InlineKeyboardButton("✨ Style Requests", callback_data="adm:styles")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="adm:settings")],
    ])


def admin_back_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")],
    ])


def admin_products_menu():
    rows = get_products()
    buttons = []
    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"{row['name']} — {money(row['price'])}",
                callback_data=f"adm:viewp:{row['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("➕ Add Product", callback_data="adm:addproduct")])
    buttons.append([InlineKeyboardButton("✏️ Edit Product", callback_data="adm:editproduct")])
    buttons.append([InlineKeyboardButton("🗑️ Delete Product", callback_data="adm:deleteproduct")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")])
    return InlineKeyboardMarkup(buttons)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    context.user_data.pop("admin_add", None)
    context.user_data.pop("admin_edit", None)

    await update.message.reply_text(
        "👑 *ADMIN PANEL*\n\nChoose a section:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_menu(),
    )


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query

    if data == "adm:home":
        context.user_data.pop("admin_add", None)
        context.user_data.pop("admin_edit", None)
        await safe_edit_query(query, 
            "👑 *ADMIN PANEL*\n\nChoose a section:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_menu(),
        )
        return

    if data == "adm:products":
        rows = get_products()
        text = "📦 *Products*\n\n"
        if not rows:
            text += "No products yet.\n"
        else:
            for row in rows:
                text += f"`{row['id']}` | {row['name']} | {money(row['price'])}\n"
        await safe_edit_query(query, 
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_products_menu(),
        )
        return

    if data.startswith("adm:viewp:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await safe_edit_query(query, "⚠️ Product not found.", reply_markup=admin_back_button())
            return
        text = (
            f"*{product['name']}*\n"
            f"ID: `{product['id']}`\n"
            f"Category: {product['category']}\n"
            f"Price: {money(product['price'])}\n"
            f"Variety: {product['variety'] or 'Not specified'}\n"
            f"Features: {product['features'] or 'Not specified'}\n\n"
            f"Description: {product['description'] or 'Not specified'}"
        )
        await safe_edit_query(query, 
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Products", callback_data="adm:products")],
            ]),
        )
        return

    if data == "adm:addproduct":
        context.user_data["admin_add"] = {"step": "id", "data": {}}
        await safe_edit_query(query, 
            "➕ *Add Product — Step 1 of 8*\n\n"
            "Send a short unique ID for the product (e.g. `navy_hijab`).\n\n"
            "Type *cancel* anytime to stop.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "adm:editproduct":
        rows = get_products()
        if not rows:
            await safe_edit_query(query, "No products to edit yet.", reply_markup=admin_back_button())
            return
        buttons = [
            [InlineKeyboardButton(row["name"], callback_data=f"adm:editp:{row['id']}")]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:products")])
        await safe_edit_query(query, 
            "✏️ *Edit Product*\n\nSelect a product:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:editp:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await safe_edit_query(query, "⚠️ Product not found.", reply_markup=admin_back_button())
            return
        buttons = [
            [InlineKeyboardButton("Name", callback_data=f"adm:editf:{product_id}:name")],
            [InlineKeyboardButton("Category", callback_data=f"adm:editf:{product_id}:category")],
            [InlineKeyboardButton("Subcategory", callback_data=f"adm:editf:{product_id}:subcategory")],
            [InlineKeyboardButton("Variety", callback_data=f"adm:editf:{product_id}:variety")],
            [InlineKeyboardButton("Price", callback_data=f"adm:editf:{product_id}:price")],
            [InlineKeyboardButton("Description", callback_data=f"adm:editf:{product_id}:description")],
            [InlineKeyboardButton("✨ Features", callback_data=f"adm:editf:{product_id}:features")],
            [InlineKeyboardButton("📸 Main Photo", callback_data=f"adm:editphoto:{product_id}")],
            [InlineKeyboardButton("🎥 Product Video", callback_data=f"adm:editvideo:{product_id}")],
            [InlineKeyboardButton("🗑️ Remove Video", callback_data=f"adm:removevideo:{product_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:editproduct")],
        ]
        await safe_edit_query(query, 
            f"✏️ *Editing {product['name']}*\n\nWhich field?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:editvideo:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await safe_edit_query(query, "⚠️ Product not found.", reply_markup=admin_back_button())
            return
        context.user_data["video_target"] = product_id
        await safe_edit_query(query,
            f"🎥 Send the new product video for *{product['name']}*.\n\nThis replaces the current video.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data=f"adm:editp:{product_id}")]]),
        )
        return

    if data.startswith("adm:removevideo:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await safe_edit_query(query, "⚠️ Product not found.", reply_markup=admin_back_button())
            return
        c=conn(); c.execute("UPDATE products SET video_file_id='' WHERE id=?", (product_id,)); c.commit(); c.close()
        await safe_edit_query(query, "✅ Product video removed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Product", callback_data=f"adm:editp:{product_id}")]]))
        return

    if data.startswith("adm:editphoto:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await safe_edit_query(query, "⚠️ Product not found.", reply_markup=admin_back_button())
            return
        context.user_data["photo_target"] = product_id
        await safe_edit_query(query, 
            f"📸 Send the new main photo for *{product['name']}*.\n\n"
            "This replaces the current main/first product image.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data=f"adm:editp:{product_id}")]]),
        )
        return

    if data.startswith("adm:editf:"):
        _, _, product_id, field = data.split(":", 3)
        product = get_product(product_id)
        if not product:
            await safe_edit_query(query, "⚠️ Product not found.", reply_markup=admin_back_button())
            return
        context.user_data["admin_edit"] = {"id": product_id, "field": field}
        hint = " (use 0 for price-on-request)" if field == "price" else ""
        if field == "subcategory":
            valid = SUBCATEGORY_MAP.get((product["category"] or "").strip().lower())
            if valid:
                options = "\n".join(f"• {key}" for key, _ in valid)
                hint = f"\n\nValid options for *{product['category']}*:\n{options}\n\nOr type *none* to clear it."
            else:
                hint = f"\n\n_{product['category']} has no subcategories defined — type *none* to clear it._"
        await safe_edit_query(query, 
            f"✏️ Send the new *{field}* for *{product['name']}*{hint}.\n\n"
            "Type *cancel* to stop.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "adm:deleteproduct":
        rows = get_products()
        if not rows:
            await safe_edit_query(query, "No products to delete yet.", reply_markup=admin_back_button())
            return
        buttons = [
            [InlineKeyboardButton(f"🗑️ {row['name']}", callback_data=f"adm:delp:{row['id']}")]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:products")])
        await safe_edit_query(query, 
            "🗑️ *Delete Product*\n\nSelect a product to remove:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:delp:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await safe_edit_query(query, "⚠️ Product not found.", reply_markup=admin_back_button())
            return
        await safe_edit_query(query, 
            f"⚠️ Remove *{product['name']}* from the shop?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, remove", callback_data=f"adm:delconfirm:{product_id}"),
                    InlineKeyboardButton("❌ No", callback_data="adm:deleteproduct"),
                ],
            ]),
        )
        return

    if data.startswith("adm:delconfirm:"):
        product_id = data.split(":", 2)[2]
        c = conn()
        c.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))
        c.commit()
        c.close()
        await safe_edit_query(query, 
            "✅ Product removed from the shop.",
            reply_markup=admin_back_button(),
        )
        return

    if data == "adm:vieworders":
        c = conn()
        rows = c.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 10"
        ).fetchall()
        c.close()

        if not rows:
            text = "📋 *Recent Orders*\n\nNo orders yet."
        else:
            lines = ["📋 *Recent Orders (last 10)*\n"]
            for row in rows:
                order_number = f"AD-{row['id']:05d}"
                lines.append(
                    f"#{order_number} — {md_escape(row['customer_name'])} — "
                    f"{money(row['total'])} — *{fmt_status(row['status'])}*"
                )
            lines.append("\nUse /admin_orders for full details, or /status ID STATUS to update.")
            text = "\n".join(lines)

        await safe_edit_query(query, 
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_button(),
        )
        return

    if data == "adm:sales":
        c = conn()
        total_orders = c.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]

        completed_statuses = ("PAID", "PROCESSING", "OUT_FOR_DELIVERY", "DELIVERED")
        placeholders = ",".join("?" * len(completed_statuses))

        revenue_row = c.execute(
            f"SELECT COALESCE(SUM(total), 0) AS revenue, COUNT(*) AS n "
            f"FROM orders WHERE status IN ({placeholders})",
            completed_statuses,
        ).fetchone()

        pending_payment = c.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE status='AWAITING_PAYMENT'"
        ).fetchone()["n"]

        status_rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM orders GROUP BY status"
        ).fetchall()
        c.close()

        lines = [
            "📊 *Sales Summary*\n",
            f"🧾 Total orders: *{total_orders}*",
            f"💰 Revenue (paid & beyond): *{money(revenue_row['revenue'])}*",
            f"✅ Paid+ orders: *{revenue_row['n']}*",
            f"⏳ Awaiting payment: *{pending_payment}*",
            "",
            "*By status:*",
        ]
        for row in status_rows:
            lines.append(f"• {fmt_status(row['status'])}: {row['n']}")

        await safe_edit_query(query, 
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_button(),
        )
        return

    if data == "adm:delivery":
        c = conn()
        method_rows = c.execute(
            "SELECT delivery_method, COUNT(*) AS n FROM orders GROUP BY delivery_method"
        ).fetchall()
        ready_rows = c.execute(
            "SELECT * FROM orders WHERE status IN ('PAID','PROCESSING') "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
        c.close()

        lines = ["🚚 *Delivery Overview*\n", "*By method:*"]
        for row in method_rows:
            method = row["delivery_method"] or "pickup"
            lines.append(f"• {DELIVERY_LABELS.get(method, method)}: {row['n']}")

        lines.append("\n*Ready to dispatch (PAID / PROCESSING):*")
        if not ready_rows:
            lines.append("None right now.")
        else:
            for row in ready_rows:
                order_number = f"AD-{row['id']:05d}"
                method = row["delivery_method"] or "pickup"
                destination = ADDRESS if method == "pickup" else f"{md_escape(row['address'])}, {md_escape(row['city'])}"
                lines.append(
                    f"#{order_number} — {md_escape(row['customer_name'])} — "
                    f"{DELIVERY_LABELS.get(method, method)} — {destination}"
                )

        buttons = []
        for row in ready_rows[:5]:
            order_number = f"AD-{row['id']:05d}"
            buttons.append([
                InlineKeyboardButton(
                    f"🚚 #{order_number} → OUT FOR DELIVERY",
                    callback_data=f"adm:setstatus:{row['id']}:OUT_FOR_DELIVERY",
                ),
                InlineKeyboardButton(
                    f"✅ → DELIVERED",
                    callback_data=f"adm:setstatus:{row['id']}:DELIVERED",
                ),
            ])
        buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")])

        await safe_edit_query(query, 
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:setstatus:"):
        _, _, order_id_text, new_status = data.split(":", 3)
        try:
            order_id = int(order_id_text)
        except ValueError:
            await safe_edit_query(query, "⚠️ Invalid order.", reply_markup=admin_back_button())
            return

        row = await update_order_status(context, order_id, new_status)
        if not row:
            await safe_edit_query(query, "⚠️ Order not found.", reply_markup=admin_back_button())
            return

        await handle_admin_callback(update, context, "adm:delivery")
        return

    if data.startswith("adm:payverify:"):
        parts = data.split(":")
        try:
            order_id = int(parts[2])
        except (ValueError, IndexError):
            await safe_edit_query(query, "⚠️ Invalid order.", reply_markup=admin_back_button())
            return
        approved = len(parts) > 3 and parts[3] == "yes"
        row = await verify_payment(context, order_id, approved)
        if not row:
            await safe_edit_query(query, "⚠️ Order not found.", reply_markup=admin_back_button())
            return
        order_number = f"AD-{order_id:05d}"
        if approved:
            text = f"✅ *Payment verified* for #{order_number}.\n\nCustomer has been notified and the order is now PAID."
        else:
            text = f"⚠️ Payment for #{order_number} was not verified.\n\nThe customer has been notified that payment is still awaiting verification."
        await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button())
        return

    if data.startswith("adm:order:"):
        try:
            order_id = int(data.split(":", 2)[2])
        except ValueError:
            await safe_edit_query(query, "⚠️ Invalid order.", reply_markup=admin_back_button())
            return
        c = conn()
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        c.close()
        if not row:
            await safe_edit_query(query, "⚠️ Order not found.", reply_markup=admin_back_button())
            return
        order_number = f"AD-{order_id:05d}"
        text = (
            f"📦 *Order #{order_number}*\n\n"
            f"👤 {md_escape(row['customer_name'])}\n📞 {md_escape(row['phone'])}\n"
            f"🚚 {DELIVERY_LABELS.get(row['delivery_method'] or 'pickup', row['delivery_method'] or 'pickup')}\n"
            f"📍 {md_escape(row['address'])}\n🏙️ {md_escape(row['city']) or '-'}, {md_escape(row['state']) or '-'}\n\n"
            f"Items:\n{md_escape(row['items'])}\n\n"
            f"💰 *{money(row['total'])}*\n"
            f"Status: *{fmt_status(row['status'])}*\n"
            f"Payment receipt: {'✅ Received' if row['receipt_file_id'] else '— Not attached'}"
        )
        buttons = [
            [InlineKeyboardButton("💳 Verify Payment", callback_data=f"adm:payverify:{order_id}:yes")],
            [InlineKeyboardButton("🛠️ Processing", callback_data=f"adm:setstatus:{order_id}:PROCESSING")],
            [InlineKeyboardButton("🛵 Out for Delivery", callback_data=f"adm:setstatus:{order_id}:OUT_FOR_DELIVERY")],
            [InlineKeyboardButton("⚠️ Delivery Failed", callback_data=f"adm:setstatus:{order_id}:DELIVERY_FAILED")],
            [InlineKeyboardButton("✅ Delivered", callback_data=f"adm:setstatus:{order_id}:DELIVERED")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data=f"adm:setstatus:{order_id}:CANCELLED")],
            [InlineKeyboardButton("⬅️ Orders", callback_data="adm:vieworders")],
        ]
        await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "adm:payments":
        c = conn()
        rows = c.execute(
            "SELECT * FROM orders WHERE payment_notified=1 OR receipt_file_id!='' ORDER BY id DESC LIMIT 10"
        ).fetchall()
        c.close()
        lines = ["💳 *Payment Verification*\n"]
        buttons = []
        if not rows:
            lines.append("No customer payment notifications yet.")
        else:
            for row in rows:
                order_number = f"AD-{row['id']:05d}"
                lines.append(f"#{order_number} — {md_escape(row['customer_name'])} — {money(row['total'])} — *{fmt_status(row['status'])}*")
                buttons.append([InlineKeyboardButton(f"🔎 Review #{order_number}", callback_data=f"adm:order:{row['id']}")])
        buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")])
        await safe_edit_query(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "adm:marketing":
        text = (
            "📣 *Marketing & Launch*\n\n"
            "Use the commands below from your admin account:\n\n"
            "*/launch* — Generate the official launch message with Shop, Website and WhatsApp buttons.\n\n"
            "*/broadcast MESSAGE* — Send a promotion/announcement to customers who have ordered before.\n"
            "*/campaigns* — View recent promotion performance.\n\n"
            "Reusable campaign ideas: Launch • Weekend Offer • New Arrival • Eid/Ramadan • Customer Appreciation • Referral • Flash Sale • Bundle • Repeat Customer.\n\n"
            "Example:\n`/broadcast 🖤 New arrivals are now available! Tap /start to shop.`"
        )
        await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button())
        return

    if data == "adm:weekly":
        c=conn()
        total_orders=c.execute("SELECT COUNT(*) FROM orders WHERE created_at >= datetime('now','-7 days')").fetchone()[0]
        completed=c.execute("SELECT COUNT(*) FROM orders WHERE status='DELIVERED' AND created_at >= datetime('now','-7 days')").fetchone()[0]
        cancelled=c.execute("SELECT COUNT(*) FROM orders WHERE status='CANCELLED' AND created_at >= datetime('now','-7 days')").fetchone()[0]
        revenue=c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status IN ('PAID','PROCESSING','OUT_FOR_DELIVERY','DELIVERED') AND created_at >= datetime('now','-7 days')").fetchone()[0]
        customers=c.execute("SELECT COUNT(*) FROM customers WHERE first_seen >= datetime('now','-7 days')").fetchone()[0]
        reviews=c.execute("SELECT COUNT(*), COALESCE(AVG(rating),0) FROM reviews WHERE created_at >= datetime('now','-7 days')").fetchone()
        top=c.execute("""SELECT product_name, SUM(quantity) AS qty FROM order_items oi JOIN orders o ON o.id=oi.order_id
                         WHERE o.status IN ('PAID','PROCESSING','OUT_FOR_DELIVERY','DELIVERED')
                         AND o.created_at >= datetime('now','-7 days') GROUP BY product_id, product_name ORDER BY qty DESC LIMIT 5""").fetchall()
        referrals=c.execute("SELECT COUNT(*) FROM customers WHERE source='referral' AND first_seen >= datetime('now','-7 days')").fetchone()[0]
        paid=c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('PAID','PROCESSING','OUT_FOR_DELIVERY','DELIVERED') AND created_at >= datetime('now','-7 days')").fetchone()[0]
        unpaid=c.execute("SELECT COUNT(*) FROM orders WHERE status='AWAITING_PAYMENT' AND created_at >= datetime('now','-7 days')").fetchone()[0]
        delivery_failed=c.execute("SELECT COUNT(*) FROM orders WHERE status='DELIVERY_FAILED' AND created_at >= datetime('now','-7 days')").fetchone()[0]
        campaign=c.execute("SELECT COALESCE(SUM(sent),0), COALESCE(SUM(failed),0) FROM campaigns WHERE created_at >= datetime('now','-7 days')").fetchone()
        c.close()
        best="\n".join([f"• {md_escape(r['product_name'])} × {r['qty']}" for r in top]) or "• No product sales recorded yet"
        text=("📈 *7-Day Business Review*\n\n" f"📦 Orders: {total_orders}\n" f"✅ Delivered: {completed}\n" f"❌ Cancelled: {cancelled}\n" f"💰 Confirmed sales: {money(revenue)}\n" f"💳 Paid/confirmed orders: {paid}\n" f"⏳ Awaiting payment: {unpaid}\n" f"👥 New customers: {customers}\n" f"🎁 Referral customers: {referrals}\n" f"⚠️ Delivery failures: {delivery_failed}\n" f"📣 Campaign messages: {campaign[0]} sent / {campaign[1]} failed\n" f"⭐ Reviews: {reviews[0]} | Avg rating: {reviews[1]:.1f}/5\n\n" f"🏆 *Best-selling products*\n{best}\n\n" "Use this report weekly to compare sales, delivery, customer growth, promotions and feedback.")
        await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button()); return

    if data == "adm:support":
        c=conn(); rows=c.execute("SELECT * FROM support_tickets ORDER BY id DESC LIMIT 10").fetchall(); c.close()
        lines=["🎧 *Recent Support Tickets*\n"]
        for r in rows: lines.append(f"#{r['id']} — @{md_escape(r['username'] or 'none')} — *{fmt_status(r['status'])}*\n{md_escape(r['message'][:180])}")
        if not rows: lines.append("No support tickets yet.")
        await safe_edit_query(query, "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button()); return

    if data == "adm:reviews":
        c=conn(); rows=c.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 10").fetchall(); avg=c.execute("SELECT COALESCE(AVG(rating),0) FROM reviews").fetchone()[0]; c.close()
        lines=[f"⭐ *Customer Reviews* — Average {avg:.1f}/5\n"]
        for r in rows: lines.append(f"Order #{r['order_id']} — {r['rating']}/5\n{md_escape(r['comment']) or 'No comment'}")
        if not rows: lines.append("No reviews yet.")
        await safe_edit_query(query, "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button()); return

    if data.startswith("adm:stylereply:"):
        rid=int(data.split(":")[2]); context.user_data["style_admin_reply"]=rid; await query.message.reply_text(f"💬 Type your reply to customer #AD-S{rid:04d} now.") ; return

    if data.startswith("adm:styleprice:"):
        rid=int(data.split(":")[2]); context.user_data["style_admin_price"]=rid; await query.message.reply_text(f"💰 Enter the agreed PRODUCT price for #AD-S{rid:04d} (numbers only).") ; return

    if data.startswith("adm:stylequote:"):
        rid=int(data.split(":")[2]); c=conn(); r=c.execute("SELECT * FROM style_requests WHERE id=?",(rid,)).fetchone(); c.close()
        if not r:
            await safe_edit_query(query, "⚠️ Not found.")
            return
        if not r["delivery_method"]:
            await safe_edit_query(query, "⚠️ Customer has not chosen delivery yet.")
            return
        context.user_data["style_admin_delivery_fee"]=rid; await query.message.reply_text(f"🚚 {STYLE_DELIVERY_LABELS.get(r['delivery_method'],r['delivery_method'])}\n\nEnter the DELIVERY FEE for #AD-S{rid:04d} (numbers only).") ; return

    if data == "adm:styles":
        c=conn(); rows=c.execute("SELECT * FROM style_requests ORDER BY id DESC LIMIT 10").fetchall(); c.close()
        lines=["✨ *Customer Style Requests*\n"]
        buttons=[]
        for r in rows:
            lines.append(f"#AD-S{r['id']:04d} — {md_escape(r['customer_name'] or 'Customer')} — *{fmt_status(r['status'])}*\n{md_escape(r['description'][:160])}")
            buttons.append([InlineKeyboardButton(f"🔎 #AD-S{r['id']:04d}", callback_data=f"adm:style:{r['id']}")])
        if not rows: lines.append("No style requests yet.")
        buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")])
        await safe_edit_query(query, "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("adm:style:"):
        parts=data.split(":")
        if len(parts)==3:
            try: request_id=int(parts[2])
            except ValueError:
                await safe_edit_query(query,"⚠️ Invalid style request.",reply_markup=admin_back_button()); return
            c=conn(); row=c.execute("SELECT * FROM style_requests WHERE id=?",(request_id,)).fetchone(); media=c.execute("SELECT * FROM style_request_media WHERE request_id=? ORDER BY id",(request_id,)).fetchall(); c.close()
            if not row:
                await safe_edit_query(query,"⚠️ Style request not found.",reply_markup=admin_back_button()); return
            text=(f"✨ *Style Request #AD-S{request_id:04d}*\n\n👤 {md_escape(row['customer_name'] or '-')}\nUsername: @{md_escape(row['username'] or 'none')}\nTelegram ID: `{row['user_id']}`\nStatus: *{fmt_status(row['status'])}*\n\n📝 {md_escape(row['description'] or '-')}\n\nMedia: {len(media)} file(s)")
            text += f"\n\n💰 Product price: {money(row['product_price'] or 0)}\n🚚 Delivery: {STYLE_DELIVERY_LABELS.get(row['delivery_method'], row['delivery_method'] or 'Not selected')}\n📦 Delivery fee: {money(row['delivery_fee'] or 0)}\n🧾 Total: {money(row['total_price'] or 0)}\nQuote: *{fmt_status(row['quote_status'])}*"
            buttons=[[InlineKeyboardButton("💬 Reply",callback_data=f"adm:stylereply:{request_id}"),InlineKeyboardButton("💰 Set Price",callback_data=f"adm:styleprice:{request_id}")],[InlineKeyboardButton("🚚 Set Delivery Fee",callback_data=f"adm:stylequote:{request_id}")],[InlineKeyboardButton("👀 Contacted",callback_data=f"adm:stylestatus:{request_id}:CONTACTED")],[InlineKeyboardButton("✅ Completed",callback_data=f"adm:stylestatus:{request_id}:COMPLETED")],[InlineKeyboardButton("📦 Close",callback_data=f"adm:stylestatus:{request_id}:CLOSED")],[InlineKeyboardButton("⬅️ Style Requests",callback_data="adm:styles")]]
            await safe_edit_query(query,text,parse_mode=ParseMode.MARKDOWN,reply_markup=InlineKeyboardMarkup(buttons))
            # Re-send stored samples when the admin opens the request.
            for m in media:
                try:
                    if m['media_type']=='photo': await context.bot.send_photo(chat_id=ADMIN_ID,photo=m['file_id'],caption=f"#AD-S{request_id:04d} customer sample")
                    elif m['media_type']=='video': await context.bot.send_video(chat_id=ADMIN_ID,video=m['file_id'],caption=f"#AD-S{request_id:04d} customer sample video")
                except Exception: logger.exception("Could not resend style request media")
            return

    if data.startswith("adm:stylestatus:"):
        parts=data.split(":")
        if len(parts)!=4:
            await safe_edit_query(query,"⚠️ Invalid style request status.",reply_markup=admin_back_button()); return
        try: request_id=int(parts[2])
        except ValueError:
            await safe_edit_query(query,"⚠️ Invalid style request.",reply_markup=admin_back_button()); return
        status=parts[3]
        if status not in {"CONTACTED","COMPLETED","CLOSED"}:
            await safe_edit_query(query,"⚠️ Invalid status.",reply_markup=admin_back_button()); return
        c=conn(); c.execute("UPDATE style_requests SET status=? WHERE id=?",(status,request_id)); c.commit(); c.close()
        await safe_edit_query(query,f"✅ Style Request #AD-S{request_id:04d} marked *{status}*.",parse_mode=ParseMode.MARKDOWN,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Style Requests",callback_data="adm:styles")],[InlineKeyboardButton("👑 Admin Panel",callback_data="adm:home")]]))
        return

    if data == "adm:colorsdone":
        context.user_data.pop("admin_color_add", None)
        await safe_edit_query(query, 
            "✅ Color/fabric photo upload finished.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎨 Manage Color/Fabric Photos", callback_data="adm:colors")],[InlineKeyboardButton("👑 Admin Panel", callback_data="adm:home")]])
        )
        return

    if data == "adm:colors":
        colors = get_color_options()
        text = (
            "🎨 *Color/Fabric Photo Library*\n\n"
            f"Available photos: *{len(colors)}*\n\n"
            "Upload the actual fabric/color photos here. You do not need to type a color name. "
            "Customers will see these photos only when ordering Hijabs or Jilbabs. Textiles and More use their own product photos."
        )
        buttons = [[InlineKeyboardButton("➕ Add Fabric/Color Photo", callback_data="adm:addcolor")]]
        for color in colors:
            buttons.append([InlineKeyboardButton(f"🖼️ Photo #{color['id']} — Remove", callback_data=f"adm:delcolor:{color['id']}")])
        buttons.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")])
        await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "adm:addcolor":
        context.user_data["admin_color_add"] = True
        await safe_edit_query(query, 
            "🎨 *Add Fabric/Color Photo*\n\nSend the actual fabric/color photo now.\n\n"
            "You do NOT need to type the color name. The photo becomes a selectable color/fabric option for Hijabs and Jilbabs only. Textiles and More use their own product photos.\n\n"
            "Type *cancel* to stop.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data="adm:colors")]])
        )
        return

    if data.startswith("adm:delcolor:"):
        try:
            color_id = int(data.split(":", 2)[2])
        except ValueError:
            await safe_edit_query(query, "⚠️ Invalid color photo.", reply_markup=admin_back_button())
            return
        color = get_color_option(color_id)
        if not color:
            await safe_edit_query(query, "⚠️ Color photo not found.", reply_markup=admin_back_button())
            return
        c = conn(); c.execute("UPDATE color_options SET active=0 WHERE id=?", (color_id,)); c.commit(); c.close()
        await safe_edit_query(query, f"✅ Fabric/color photo #{color_id} removed from customer choices.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎨 Back to Color/Fabric Photos", callback_data="adm:colors")],[InlineKeyboardButton("🏠 Admin Panel", callback_data="adm:home")]]))
        return

    if data == "adm:settings":
        accepting = is_accepting_orders()
        text = (
            "⚙️ *Settings*\n\n"
            f"🛒 Accepting new orders: *{'YES' if accepting else 'NO'}*\n\n"
            f"📞 {PHONE1}\n📞 {PHONE2}\n"
            f"📍 {ADDRESS}\n"
            f"🌐 {WEBSITE}\n\n"
            "*Payment accounts:*\n"
        )
        for bank, account_name, account_number in PAYMENT_ACCOUNTS:
            text += f"• {bank} — {account_name} — {account_number}\n"

        toggle_label = "🔴 Stop Accepting Orders" if accepting else "🟢 Resume Accepting Orders"

        await safe_edit_query(query, 
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(toggle_label, callback_data="adm:toggleorders")],
                [InlineKeyboardButton("✏️ Edit Welcome Message", callback_data="adm:editwelcome")],
                [InlineKeyboardButton("🖼️ Welcome Photo", callback_data="adm:welcomephoto")],
                [InlineKeyboardButton("🚚 Delivery Fees", callback_data="adm:deliveryfees")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")],
            ]),
        )
        return

    if data == "adm:welcomephoto":
        has_photo = bool(get_setting("welcome_photo_file_id", ""))
        text = (
            "🖼️ *Welcome Photo*\n\n"
            f"Status: *{'Set' if has_photo else 'Not set'}*\n\n"
            "When set, the welcome message is sent as the caption on this photo — "
            "one merged message, not two. Change the photo as often as you like.\n\n"
            "_Note: if your welcome message is very long (over ~1024 characters), "
            "Telegram can't fit it as a caption — the bot will send the photo and "
            "message separately in that case._"
        )
        buttons = [
            [InlineKeyboardButton("➕ Set / Change Photo", callback_data="adm:setwelcomephoto")],
        ]
        if has_photo:
            buttons.append([InlineKeyboardButton("🗑️ Remove Photo", callback_data="adm:removewelcomephoto")])
        buttons.append([InlineKeyboardButton("⬅️ Back to Settings", callback_data="adm:settings")])
        await safe_edit_query(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "adm:setwelcomephoto":
        context.user_data["admin_edit_welcome_photo"] = True
        await safe_edit_query(query, 
            "🖼️ Send the new welcome photo now, or type *cancel* to stop.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "adm:removewelcomephoto":
        set_setting("welcome_photo_file_id", "")
        await safe_edit_query(query, "✅ Welcome photo removed.", reply_markup=admin_back_button())
        return

    if data == "adm:toggleorders":
        new_value = "0" if is_accepting_orders() else "1"
        set_setting("accepting_orders", new_value)
        await handle_admin_callback(update, context, "adm:settings")
        return

    if data == "adm:deliveryfees":
        text = "🚚 *Delivery Fees*\n\nFlat fee added to the order total for each method:\n\n"
        for method in ("pickup", "bauchi", "nationwide"):
            fee = get_delivery_fee(method)
            text += f"• {DELIVERY_LABELS[method]}: *{money(fee)}*\n"

        await safe_edit_query(query,
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Pickup Fee", callback_data="adm:editfee:pickup")],
                [InlineKeyboardButton("✏️ Bauchi Delivery Fee", callback_data="adm:editfee:bauchi")],
                [InlineKeyboardButton("✏️ Nationwide Delivery Fee", callback_data="adm:editfee:nationwide")],
                [InlineKeyboardButton("⬅️ Back to Settings", callback_data="adm:settings")],
            ]),
        )
        return

    if data.startswith("adm:editfee:"):
        method = data.split(":", 2)[2]
        if method not in DELIVERY_LABELS:
            await safe_edit_query(query, "⚠️ Unknown delivery method.", reply_markup=admin_back_button())
            return
        current_fee = get_delivery_fee(method)
        context.user_data["admin_edit_fee"] = method
        await safe_edit_query(query,
            f"✏️ *{DELIVERY_LABELS[method]}*\n\n"
            f"Current fee: *{money(current_fee)}*\n\n"
            "Send the new fee as a number (e.g. `1000`, or `0` for free), or type *cancel* to leave it unchanged.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "adm:editwelcome":
        current = get_setting("welcome_message", DEFAULT_WELCOME_MESSAGE)
        context.user_data["admin_edit_welcome"] = True
        await safe_edit_query(query,
            "✏️ *Edit Welcome Message*\n\n"
            "This is the text shown after the store name/tagline on /start. "
            f"Current version:\n\n{md_escape(current)}\n\n"
            "Send the new text now, or type *reset* to restore the default, "
            "or *cancel* to leave it unchanged.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await safe_edit_query(query, "⚠️ Unknown admin action.", reply_markup=admin_back_button())


async def handle_admin_add_step(message, context: ContextTypes.DEFAULT_TYPE, admin_add: dict, text: str):
    step = admin_add["step"]
    data = admin_add["data"]

    if step == "id":
        product_id = text.strip().lower().replace(" ", "_")
        if not product_id:
            await message.reply_text("Please send a valid ID.")
            return
        if get_product(product_id):
            await message.reply_text("That ID already exists. Send a different one.")
            return
        data["id"] = product_id
        admin_add["step"] = "name"
        await message.reply_text("➕ *Step 2 of 8*\n\nSend the product name.", parse_mode=ParseMode.MARKDOWN)
        return

    if step == "name":
        if not text.strip():
            await message.reply_text("Please send a valid name.")
            return
        data["name"] = text.strip()
        admin_add["step"] = "category"
        await message.reply_text(
            "➕ *Step 3 of 8*\n\nSend the category (e.g. hijabs, jilbabs, textiles, more).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "category":
        if not text.strip():
            await message.reply_text("Please send a valid category.")
            return
        data["category"] = text.strip().lower()
        if data["category"] in SUBCATEGORY_MAP:
            admin_add["step"] = "subcategory"
            options = "\n".join(f"• {key} — {label}" for key, label in SUBCATEGORY_MAP[data["category"]])
            await message.reply_text(
                f"➕ *Step 4 — Subcategory*\n\nType one of these keys:\n\n{options}\n\n"
                "Or type *none* to skip.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            data["subcategory"] = ""
            admin_add["step"] = "variety"
            await message.reply_text(
                "➕ *Step 4 of 8 — Variety*\n\nSend the available variety, such as colors, sizes, materials, styles, or variants.\n\nExample: Royal Blue, Black, Wine | Free Size",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    if step == "subcategory":
        category = data.get("category", "")
        valid_keys = {key for key, _ in SUBCATEGORY_MAP.get(category, [])}
        chosen = text.strip().lower().replace(" ", "_")
        if chosen == "none":
            data["subcategory"] = ""
        elif chosen in valid_keys:
            data["subcategory"] = chosen
        else:
            options = "\n".join(f"• {key} — {label}" for key, label in SUBCATEGORY_MAP.get(category, []))
            await message.reply_text(
                f"⚠️ Not a valid option. Type one of these keys:\n\n{options}\n\nOr type *none* to skip.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        admin_add["step"] = "variety"
        await message.reply_text(
            "➕ *Step 5 of 8 — Variety*\n\nSend the available variety, such as colors, sizes, materials, styles, or variants.\n\nExample: Royal Blue, Black, Wine | Free Size",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "variety":
        if not text.strip():
            await message.reply_text("Please send the product variety, or type *none* if it has no variants.", parse_mode=ParseMode.MARKDOWN)
            return
        data["variety"] = "" if text.strip().lower() == "none" else text.strip()
        admin_add["step"] = "price"
        await message.reply_text(
            "➕ *Step 5 of 8 — Price*\n\nSend the price as a number (e.g. 24000), or 0 for price-on-request.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "price":
        try:
            price_value = None if text.strip() == "0" else int(
                text.replace(",", "").replace("₦", "").strip()
            )
        except ValueError:
            await message.reply_text("Invalid price. Send a number, e.g. 24000, or 0.")
            return
        data["price"] = price_value
        admin_add["step"] = "description"
        await message.reply_text(
            "➕ *Step 6 of 8*\n\nSend the product description. Include features, material, available varieties/colors/sizes, and important details customers should see.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "description":
        if not text.strip():
            await message.reply_text("Please send a product description.")
            return
        data["description"] = text.strip()
        admin_add["step"] = "features"
        await message.reply_text(
            "➕ *Step 7 of 8 — Features*\n\nSend the key product features/benefits.\n\nExample: Lightweight, breathable, soft texture, easy to style, premium finish.\n\nType *none* if you want to skip.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "features":
        data["features"] = "" if text.strip().lower() == "none" else text.strip()
        admin_add["step"] = "photo"
        await message.reply_text(
            "➕ *Step 8 of 8 — Product Photo*\n\n"
            "Now send the product photo.\n"
            "📸 This becomes the main/first product image customers see when they open the product.\n\n"
            "If you want to add it later, type *skip*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "photo":
        if text.lower() != "skip":
            await message.reply_text(
                "📸 Please send the product as a photo, or type *skip*.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        data["photo_file_id"] = None
        c = conn()
        c.execute(
            """INSERT OR REPLACE INTO products
               (id, name, category, subcategory, price, description, variety, features, photo_file_id, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (data["id"], data["name"], data["category"], data.get("subcategory", ""), data["price"], data["description"], data.get("variety", ""), data.get("features", ""), None),
        )
        c.commit(); c.close()
        admin_add["step"] = "video"
        await message.reply_text(
            f"📸 No main photo added.\n\n🎥 Send the product video now, or type *skip*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "video":
        if text.strip().lower() == "skip":
            await finalize_admin_add(message, context, data)
        else:
            await message.reply_text("🎥 Please send the product as a video, or type *skip*.")
        return

async def finalize_admin_add(message, context, data):
    context.user_data.pop("admin_add", None)
    await message.reply_text(
        f"✅ *{data['name']}* added to the shop.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_back_button(),
    )


async def handle_admin_edit_step(message, context: ContextTypes.DEFAULT_TYPE, admin_edit: dict, text: str):
    product_id = admin_edit["id"]
    field = admin_edit["field"]

    if not get_product(product_id):
        context.user_data.pop("admin_edit", None)
        await message.reply_text("That product no longer exists.")
        return

    value = text.strip()

    if field == "price":
        try:
            value = None if value == "0" else int(value.replace(",", "").replace("₦", "").strip())
        except ValueError:
            await message.reply_text("Invalid price. Send a number, e.g. 24000, or 0.")
            return
    elif field == "category":
        value = value.lower()
    elif field == "subcategory":
        product = get_product(product_id)
        current_category = (product["category"] or "").strip().lower()
        chosen = value.lower().replace(" ", "_")
        if chosen == "none":
            value = ""
        else:
            valid_keys = {key for key, _ in SUBCATEGORY_MAP.get(current_category, [])}
            if valid_keys and chosen not in valid_keys:
                options = "\n".join(f"• {key}" for key in valid_keys)
                await message.reply_text(
                    f"⚠️ Not a valid option for *{current_category}*. Choose one:\n\n{options}\n\nOr type *none*.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            value = chosen

    if field not in ("price", "subcategory") and not value:
        await message.reply_text(f"Please send a valid {field}.")
        return

    c = conn()
    c.execute(f"UPDATE products SET {field}=? WHERE id=?", (value, product_id))
    c.commit()
    c.close()

    context.user_data.pop("admin_edit", None)

    await message.reply_text(
        f"✅ Updated *{field}*.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_back_button(),
    )


# -------------------- HEALTH SERVER --------------------

app_flask = Flask(__name__)


@app_flask.get("/")
def health():
    return {
        "status": "ok",
        "service": NAME,
        "phase": "9",
    }


def health_server():
    app_flask.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False,
    )


# -------------------- MAIN --------------------

def main():
    logger.info("Using database file: %s", os.path.abspath(DB))
    init_db()

    Thread(
        target=health_server,
        daemon=True,
    ).start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    commands = [
        ("start", start),
        ("help", help_cmd),
        ("shop", shop),
        ("cart", cart_cmd),
        ("orders", orders_cmd),
        ("contact", contact_cmd),
        ("admin", admin_command),
        ("admin_products", admin_products),
        ("addproduct", addproduct),
        ("deleteproduct", deleteproduct),
        ("setphoto", setphoto),
        ("setvideo", setvideo),
        ("admin_orders", admin_orders),
        ("status", status_command),
        ("broadcast", broadcast_command),
        ("campaigns", campaigns_command),
        ("launch", launch_command),
        ("faq", faq_cmd),
        ("support", support_cmd),
        ("referral", referral_cmd),
        ("review", review_command),
    ]

    for command, handler in commands:
        app.add_handler(
            CommandHandler(command, handler)
        )

    # Global error handler: exposes the real exception traceback in Render logs.
    app.add_error_handler(error_handler)

    app.add_handler(
        CallbackQueryHandler(buttons)
    )

    app.add_handler(
        MessageHandler(
            filters.VIDEO,
            video,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
            text_handler,
        )
    )

    logger.info("Starting AD Fashion Hijabs & More bot...")
    logger.info("Health server configured on port %s", PORT)
    logger.info("Starting Telegram polling. Do not call Telegram getUpdates manually while this bot is running.")

    try:
        # Managed lifecycle: initialize, polling, and graceful shutdown are handled together.
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
            close_loop=False,
        )
    except Exception:
        logger.exception("FATAL BOT STARTUP/POLLING ERROR")
        raise
    finally:
        logger.info("AD Fashion Hijabs & More bot is shutting down.")


if __name__ == "__main__":
    main()
