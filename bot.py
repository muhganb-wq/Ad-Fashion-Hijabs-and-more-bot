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
DB = "shop.db"

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


def get_products(category=None):
    c = conn()
    if category:
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
        [InlineKeyboardButton("🎁 Refer a Friend", callback_data="referral")],
        [InlineKeyboardButton("🌐 Visit Website", url=WEBSITE)],
    ])

def back_menu(callback_data="home"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data=callback_data)],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home")],
    ])


def category_menu(category):
    rows = get_products(category)
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

    buttons.append([
        InlineKeyboardButton("🏠 Main Menu", callback_data="home")
    ])

    return InlineKeyboardMarkup(buttons)


def cart_total(cart):
    total = 0
    unknown = False

    for product_id, quantity in cart.items():
        product = get_product(product_id)
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

    for product_id, quantity in cart.items():
        product = get_product(product_id)
        if not product:
            continue

        if product["price"] is None:
            subtotal = "Price on request"
            unknown = True
        else:
            subtotal_value = product["price"] * quantity
            subtotal = money(subtotal_value)
            total += subtotal_value

        lines.append(
            f"• {product['name']} × {quantity} — {subtotal}"
        )

    return lines, (None if unknown else total)


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


# -------------------- USER COMMANDS --------------------

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
    text=(f"🖤 *{NAME}*\n\n*{TAGLINE}*{vip_line}\n\n"
          "Welcome to your online modest-fashion store.\n\n"
          "🛍️ Browse products\n🛒 Add to cart\n💳 Pay securely\n🚚 Choose delivery or pickup\n🎧 Get support anytime\n\n"
          "Choose an option below:")
    if update.message:
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
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("📍 I Received My Order", callback_data=f"delivery_confirm:{order_id}")],[InlineKeyboardButton("🎧 Support", callback_data="support")]])
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
        await query.edit_message_text("⚠️ Invalid order number.", reply_markup=main_menu())
        return

    c = conn()
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        c.close()
        await query.edit_message_text("⚠️ Order not found.", reply_markup=main_menu())
        return

    if row["user_id"] != update.effective_user.id:
        c.close()
        await query.edit_message_text("⚠️ This order does not belong to you.", reply_markup=main_menu())
        return

    if row["payment_notified"]:
        c.close()
        await query.edit_message_text(
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

    await query.edit_message_text(
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
        await update.callback_query.edit_message_text(
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
        text += f"*Total: {money(total)}*"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧾 Checkout", callback_data="checkout")],
            [InlineKeyboardButton("🗑️ Clear Cart", callback_data="clear")],
            [InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop")],
        ])

    if update.callback_query:
        await update.callback_query.edit_message_text(
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
            await update.callback_query.edit_message_text(
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

    text = (
        "🧾 *Confirm your order*\n\n"
        f"👤 Customer: {md_escape(name)}\n"
        f"📞 Phone: {md_escape(phone)}\n"
        + "\n".join(delivery_lines)
        + "\n\n*Items:*\n"
        + "\n".join(lines)
        + f"\n\n💰 *Total: {money(total)}*"
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
            total or 0,
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
    for pid, qty in cart.items():
        product = get_product(pid)
        if product:
            c.execute("INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price) VALUES(?,?,?,?,?)",
                      (order_id, pid, product["name"], qty, product["price"] or 0))
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

    admin_text = (
        f"🔔 *NEW ORDER #{order_number}*\n\n"
        f"👤 Customer: {md_escape(customer_name)}\n"
        f"Username: @{md_escape(username or 'none')}\n"
        f"Telegram ID: `{user.id}`\n"
        f"📞 Phone: {md_escape(phone)}\n"
        + "\n".join(delivery_summary)
        + f"\n\n*Items:*\n{items}\n\n"
        f"💰 *Total: {money(total)}*\n"
        f"📦 Status: *AWAITING PAYMENT*\n\n"
        "💳 Customer should complete payment using the payment options shown by the bot."
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Could not send admin order notification.")

    clear_checkout_data(context)

    payment_text = payment_instructions(order_number, total)
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

    if lower == "cancel":
        in_admin_flow = bool(
            context.user_data.get("admin_add") or context.user_data.get("admin_edit")
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
            await query.edit_message_text(
                f"🖤 *{NAME}*\n\n"
                f"*{TAGLINE}*\n\n"
                "Choose an option:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data == "shop":
            await query.edit_message_text(
                "🛍️ *Choose a collection:*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data.startswith("cat:"):
            category = data.split(":", 1)[1]
            labels = {
                "hijabs": "🧕 Hijabs",
                "jilbabs": "👗 Jilbabs",
                "textiles": "🧵 Textiles",
                "more": "✨ More",
            }

            await query.edit_message_text(
                f"*{labels.get(category, category)}*\n\n"
                "Select a product:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=category_menu(category),
            )

        elif data.startswith("product:"):
            product_id = data.split(":", 1)[1]
            product = get_product(product_id)

            if not product:
                await query.edit_message_text(
                    "Product not found.",
                    reply_markup=main_menu(),
                )
                return

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛒 Add to Cart",
                        callback_data=f"add:{product['id']}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⚡ Order Now",
                        callback_data=f"add:{product['id']}:now",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data=f"cat:{product['category']}",
                    )
                ],
            ])

            details = [f"*{product['name']}*", "", product["description"] or ""]
            if product["variety"]:
                details += ["", f"🎨 *Variety:* {product['variety']}"]
            if product["features"]:
                details += ["", f"✨ *Features:* {product['features']}"]
            details += ["", f"💰 *{money(product['price'])}*"]
            text = "\n".join(details)

            if product["photo_file_id"]:
                await query.message.reply_photo(
                    photo=product["photo_file_id"],
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard,
                )
                try:
                    await query.message.delete()
                except Exception:
                    pass
            else:
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard,
                )

        elif data.startswith("add:"):
            parts = data.split(":")
            product_id = parts[1]
            order_now = len(parts) > 2 and parts[2] == "now"

            context.user_data.setdefault("cart", {})
            cart = context.user_data["cart"]
            cart[product_id] = cart.get(product_id, 0) + 1

            if order_now:
                await checkout(update, context)
            else:
                await query.edit_message_text(
                    "✅ *Added to your cart.*",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton(
                                "🛒 View Cart",
                                callback_data="cart",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🛍️ Continue Shopping",
                                callback_data="shop",
                            )
                        ],
                    ]),
                )

        elif data == "cart":
            await show_cart(update, context)

        elif data == "checkout":
            await checkout(update, context)

        elif data == "clear":
            context.user_data["cart"] = {}
            await query.edit_message_text(
                "🛒 Your cart is empty.",
                reply_markup=main_menu(),
            )

        elif data == "confirm_order":
            await create_order(update, context)

        elif data == "cancel_order":
            context.user_data.clear()
            await query.edit_message_text(
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
                await query.edit_message_text("⚠️ Unknown delivery method.", reply_markup=main_menu())
                return
            await query.edit_message_text(
                f"🚚 *{DELIVERY_LABELS[method]}* selected.",
                parse_mode=ParseMode.MARKDOWN,
            )
            await start_delivery_details(update, context, method)

        elif data == "help":
            await help_cmd(update, context)

        elif data == "support":
            await support_cmd(update, context)

        elif data == "support_cancel":
            context.user_data.pop("support_ticket", None)
            await query.edit_message_text("Support request cancelled.", reply_markup=main_menu())

        elif data == "referral":
            user=update.effective_user; upsert_customer(user, source="referral")
            c=conn(); referral_count=c.execute("SELECT COUNT(*) FROM customers WHERE referrer_id=?",(user.id,)).fetchone()[0]; c.close()
            await query.edit_message_text(
                "🎁 *Refer a Friend*\n\n"
                f"Share this link with a friend:\n\n{referral_link(context,user.id)}\n\n"
                f"👥 Referrals recorded: {referral_count}\n\n"
                "When they start the bot through your link, the referral is recorded. 🖤✨",
                parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

        elif data.startswith("review:"):
            _, order_id_text, rating_text=data.split(":",2)
            try: order_id=int(order_id_text); rating=int(rating_text)
            except ValueError: await query.edit_message_text("Invalid review.", reply_markup=main_menu()); return
            c=conn(); row=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,update.effective_user.id)).fetchone()
            if not row:
                c.close(); await query.edit_message_text("Order not found.", reply_markup=main_menu()); return
            c.execute("INSERT INTO reviews(order_id,user_id,rating,comment) VALUES(?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET rating=excluded.rating",(order_id,update.effective_user.id,rating,"")); c.commit(); c.close()
            context.user_data["review_order"]=order_id
            await query.edit_message_text(f"⭐ Thank you for rating Order #AD-{order_id:05d} {rating}/5.\n\nYou may now send a short comment, or type *skip*.", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

        elif data.startswith("reorder:"):
            try: order_id=int(data.split(":",1)[1])
            except ValueError: await query.edit_message_text("Invalid order.", reply_markup=main_menu()); return
            c=conn(); row=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,update.effective_user.id)).fetchone(); c.close()
            if not row or not row["product_ids"]:
                await query.edit_message_text("Some products from this order are no longer available. Please shop again.", reply_markup=main_menu()); return
            cart=context.user_data.setdefault("cart", {})
            added=0
            for pid in row["product_ids"].split(","):
                if get_product(pid): cart[pid]=cart.get(pid,0)+1; added+=1
            await query.edit_message_text(f"🛒 Added {added} item(s) from Order #AD-{order_id:05d} to your cart.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 View Cart", callback_data="cart")],[InlineKeyboardButton("🛍️ Shop More", callback_data="shop")]]))

        elif data.startswith("delivery_confirm:"):
            try: order_id=int(data.split(":",1)[1])
            except ValueError: await query.edit_message_text("Invalid order.", reply_markup=main_menu()); return
            c=conn(); row=c.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,update.effective_user.id)).fetchone(); c.close()
            if row:
                try: await context.bot.send_message(chat_id=ADMIN_ID, text=f"📍 Customer says Order #AD-{order_id:05d} has been received. Please confirm delivery.")
                except Exception: logger.exception("Could not notify admin of delivery confirmation")
                await query.edit_message_text("✅ Thank you. We've notified our team.", reply_markup=main_menu())
            else: await query.edit_message_text("Order not found.", reply_markup=main_menu())

        elif data == "orders":
            await orders_cmd(update, context)

        elif data == "contact":
            await query.edit_message_text(
                f"📞 *{NAME}*\n\n"
                f"{PHONE1}\n"
                f"{PHONE2}\n\n"
                f"WhatsApp: https://wa.me/{WHATSAPP}\n"
                f"Website: {WEBSITE}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu(),
            )

        elif data == "location":
            await query.edit_message_text(
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
            await query.edit_message_text(
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


async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
               (id, name, category, price, description, variety, features, photo_file_id, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (data["id"], data["name"], data["category"], data["price"], data["description"], data.get("variety", ""), data.get("features", ""), file_id),
        )
        c.commit(); c.close()
        context.user_data.pop("admin_add", None)
        await update.message.reply_text(
            f"✅ *{data['name']}* added to the shop.\n📸 Main product photo saved.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button(),
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
            delivery_line += f" — {row['city']}" + (f", {row['state']}" if row["state"] else "")

        text = (
            f"📦 *Order #{order_number}*\n"
            f"Customer: {md_escape(row['customer_name'])}\n"
            f"Phone: {row['phone']}\n"
            f"Address: {row['address']}\n"
            f"{delivery_line}\n\n"
            f"Items:\n{row['items']}\n\n"
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
    if new_status == "DELIVERED":
        await send_retention_followup(context, row)
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
        await query.edit_message_text(
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
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_products_menu(),
        )
        return

    if data.startswith("adm:viewp:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await query.edit_message_text("⚠️ Product not found.", reply_markup=admin_back_button())
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
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Products", callback_data="adm:products")],
            ]),
        )
        return

    if data == "adm:addproduct":
        context.user_data["admin_add"] = {"step": "id", "data": {}}
        await query.edit_message_text(
            "➕ *Add Product — Step 1 of 8*\n\n"
            "Send a short unique ID for the product (e.g. `navy_hijab`).\n\n"
            "Type *cancel* anytime to stop.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "adm:editproduct":
        rows = get_products()
        if not rows:
            await query.edit_message_text("No products to edit yet.", reply_markup=admin_back_button())
            return
        buttons = [
            [InlineKeyboardButton(row["name"], callback_data=f"adm:editp:{row['id']}")]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:products")])
        await query.edit_message_text(
            "✏️ *Edit Product*\n\nSelect a product:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:editp:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await query.edit_message_text("⚠️ Product not found.", reply_markup=admin_back_button())
            return
        buttons = [
            [InlineKeyboardButton("Name", callback_data=f"adm:editf:{product_id}:name")],
            [InlineKeyboardButton("Category", callback_data=f"adm:editf:{product_id}:category")],
            [InlineKeyboardButton("Variety", callback_data=f"adm:editf:{product_id}:variety")],
            [InlineKeyboardButton("Price", callback_data=f"adm:editf:{product_id}:price")],
            [InlineKeyboardButton("Description", callback_data=f"adm:editf:{product_id}:description")],
            [InlineKeyboardButton("✨ Features", callback_data=f"adm:editf:{product_id}:features")],
            [InlineKeyboardButton("📸 Main Photo", callback_data=f"adm:editphoto:{product_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:editproduct")],
        ]
        await query.edit_message_text(
            f"✏️ *Editing {product['name']}*\n\nWhich field?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:editphoto:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await query.edit_message_text("⚠️ Product not found.", reply_markup=admin_back_button())
            return
        context.user_data["photo_target"] = product_id
        await query.edit_message_text(
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
            await query.edit_message_text("⚠️ Product not found.", reply_markup=admin_back_button())
            return
        context.user_data["admin_edit"] = {"id": product_id, "field": field}
        hint = " (use 0 for price-on-request)" if field == "price" else ""
        await query.edit_message_text(
            f"✏️ Send the new *{field}* for *{product['name']}*{hint}.\n\n"
            "Type *cancel* to stop.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "adm:deleteproduct":
        rows = get_products()
        if not rows:
            await query.edit_message_text("No products to delete yet.", reply_markup=admin_back_button())
            return
        buttons = [
            [InlineKeyboardButton(f"🗑️ {row['name']}", callback_data=f"adm:delp:{row['id']}")]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:products")])
        await query.edit_message_text(
            "🗑️ *Delete Product*\n\nSelect a product to remove:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:delp:"):
        product_id = data.split(":", 2)[2]
        product = get_product(product_id)
        if not product:
            await query.edit_message_text("⚠️ Product not found.", reply_markup=admin_back_button())
            return
        await query.edit_message_text(
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
        await query.edit_message_text(
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

        await query.edit_message_text(
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

        await query.edit_message_text(
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
                destination = ADDRESS if method == "pickup" else f"{row['address']}, {row['city']}"
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

        await query.edit_message_text(
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
            await query.edit_message_text("⚠️ Invalid order.", reply_markup=admin_back_button())
            return

        row = await update_order_status(context, order_id, new_status)
        if not row:
            await query.edit_message_text("⚠️ Order not found.", reply_markup=admin_back_button())
            return

        await handle_admin_callback(update, context, "adm:delivery")
        return

    if data.startswith("adm:payverify:"):
        parts = data.split(":")
        try:
            order_id = int(parts[2])
        except (ValueError, IndexError):
            await query.edit_message_text("⚠️ Invalid order.", reply_markup=admin_back_button())
            return
        approved = len(parts) > 3 and parts[3] == "yes"
        row = await verify_payment(context, order_id, approved)
        if not row:
            await query.edit_message_text("⚠️ Order not found.", reply_markup=admin_back_button())
            return
        order_number = f"AD-{order_id:05d}"
        if approved:
            text = f"✅ *Payment verified* for #{order_number}.\n\nCustomer has been notified and the order is now PAID."
        else:
            text = f"⚠️ Payment for #{order_number} was not verified.\n\nThe customer has been notified that payment is still awaiting verification."
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button())
        return

    if data.startswith("adm:order:"):
        try:
            order_id = int(data.split(":", 2)[2])
        except ValueError:
            await query.edit_message_text("⚠️ Invalid order.", reply_markup=admin_back_button())
            return
        c = conn()
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        c.close()
        if not row:
            await query.edit_message_text("⚠️ Order not found.", reply_markup=admin_back_button())
            return
        order_number = f"AD-{order_id:05d}"
        text = (
            f"📦 *Order #{order_number}*\n\n"
            f"👤 {md_escape(row['customer_name'])}\n📞 {md_escape(row['phone'])}\n"
            f"🚚 {DELIVERY_LABELS.get(row['delivery_method'] or 'pickup', row['delivery_method'] or 'pickup')}\n"
            f"📍 {row['address']}\n🏙️ {row['city'] or '-'}, {row['state'] or '-'}\n\n"
            f"Items:\n{row['items']}\n\n"
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
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
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
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
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
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button())
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
        best="\n".join([f"• {r['product_name']} × {r['qty']}" for r in top]) or "• No product sales recorded yet"
        text=("📈 *7-Day Business Review*\n\n" f"📦 Orders: {total_orders}\n" f"✅ Delivered: {completed}\n" f"❌ Cancelled: {cancelled}\n" f"💰 Confirmed sales: {money(revenue)}\n" f"💳 Paid/confirmed orders: {paid}\n" f"⏳ Awaiting payment: {unpaid}\n" f"👥 New customers: {customers}\n" f"🎁 Referral customers: {referrals}\n" f"⚠️ Delivery failures: {delivery_failed}\n" f"📣 Campaign messages: {campaign[0]} sent / {campaign[1]} failed\n" f"⭐ Reviews: {reviews[0]} | Avg rating: {reviews[1]:.1f}/5\n\n" f"🏆 *Best-selling products*\n{best}\n\n" "Use this report weekly to compare sales, delivery, customer growth, promotions and feedback.")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button()); return

    if data == "adm:support":
        c=conn(); rows=c.execute("SELECT * FROM support_tickets ORDER BY id DESC LIMIT 10").fetchall(); c.close()
        lines=["🎧 *Recent Support Tickets*\n"]
        for r in rows: lines.append(f"#{r['id']} — @{md_escape(r['username'] or 'none')} — *{fmt_status(r['status'])}*\n{md_escape(r['message'][:180])}")
        if not rows: lines.append("No support tickets yet.")
        await query.edit_message_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button()); return

    if data == "adm:reviews":
        c=conn(); rows=c.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 10").fetchall(); avg=c.execute("SELECT COALESCE(AVG(rating),0) FROM reviews").fetchone()[0]; c.close()
        lines=[f"⭐ *Customer Reviews* — Average {avg:.1f}/5\n"]
        for r in rows: lines.append(f"Order #{r['order_id']} — {r['rating']}/5\n{md_escape(r['comment']) or 'No comment'}")
        if not rows: lines.append("No reviews yet.")
        await query.edit_message_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button()); return

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

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(toggle_label, callback_data="adm:toggleorders")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm:home")],
            ]),
        )
        return

    if data == "adm:toggleorders":
        new_value = "0" if is_accepting_orders() else "1"
        set_setting("accepting_orders", new_value)
        await handle_admin_callback(update, context, "adm:settings")
        return

    await query.edit_message_text("⚠️ Unknown admin action.", reply_markup=admin_back_button())


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
        admin_add["step"] = "variety"
        await message.reply_text(
            "➕ *Step 4 of 8 — Variety*\n\nSend the available variety, such as colors, sizes, materials, styles, or variants.\n\nExample: Royal Blue, Black, Wine | Free Size",
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
               (id, name, category, price, description, variety, features, photo_file_id, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (data["id"], data["name"], data["category"], data["price"], data["description"], data.get("variety", ""), data.get("features", ""), None),
        )
        c.commit(); c.close()
        context.user_data.pop("admin_add", None)
        await message.reply_text(
            f"✅ *{data['name']}* added to the shop.\n📸 No photo added. Use /setphoto {data['id']} later.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_button(),
        )
        return

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

    if field != "price" and not value:
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
        "phase": "7",
    }


def health_server():
    app_flask.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False,
    )


# -------------------- MAIN --------------------

def main():
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
