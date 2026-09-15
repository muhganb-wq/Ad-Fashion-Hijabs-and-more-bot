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
# Phase 1-3: Shop, cart, checkout, payments  (kept intact)
# Phase 4: Delivery options & customer details
# Phase 5: Professional admin panel (/admin)
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

# Payment accounts (Phase 3 — unchanged)
PAYMENT_ACCOUNTS = [
    ("OPAY", "MUHAMMAD MUHAMMAD ADAMU", "7011927516"),
    ("OPAY", "MUHAMMAD ADAMU", "9136114700"),
    ("POLARIS BANK", "MUHAMMAD ADAMU", "3095751555"),
]

# Phase 4 — delivery methods
DELIVERY_METHODS = {
    "pickup": {
        "label": "📍 Pickup — Bauchi Central Market",
        "short": "Pickup (Bauchi Central Market)",
        "fee_key": "fee_pickup",
        "default_fee": 0,
        "needs_address": False,
    },
    "bauchi": {
        "label": "🛵 Bauchi Delivery",
        "short": "Bauchi Delivery",
        "fee_key": "fee_bauchi",
        "default_fee": 1000,
        "needs_address": True,
    },
    "nationwide": {
        "label": "📦 Nationwide Delivery",
        "short": "Nationwide Delivery",
        "fee_key": "fee_nationwide",
        "default_fee": 3500,
        "needs_address": True,
    },
}

ORDER_STATUSES = [
    "PENDING",
    "AWAITING_PAYMENT",
    "PAID",
    "PROCESSING",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "CANCELLED",
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Phase 3 + Phase 4 fields. Safe for existing databases.
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(orders)").fetchall()}
    new_cols = {
        "payment_notified": "INTEGER DEFAULT 0",
        "payment_method": "TEXT DEFAULT ''",
        "receipt_file_id": "TEXT DEFAULT ''",
        "delivery_method": "TEXT DEFAULT ''",
        "city": "TEXT DEFAULT ''",
        "state": "TEXT DEFAULT ''",
        "delivery_note": "TEXT DEFAULT ''",
        "delivery_fee": "INTEGER DEFAULT 0",
        "subtotal": "INTEGER DEFAULT 0",
    }
    for col, spec in new_cols.items():
        if col not in existing_cols:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} {spec}")

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

    for method in DELIVERY_METHODS.values():
        c.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            (method["fee_key"], str(method["default_fee"])),
        )

    c.commit()
    c.close()


def get_setting(key, default=""):
    c = conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else default


def set_setting(key, value):
    c = conn()
    c.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    c.commit()
    c.close()


def delivery_fee(method_key):
    method = DELIVERY_METHODS.get(method_key)
    if not method:
        return 0
    try:
        return int(get_setting(method["fee_key"], method["default_fee"]))
    except (TypeError, ValueError):
        return method["default_fee"]


def money(value):
    if value is None:
        return "Price on request"
    return f"₦{value:,.0f}"


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
            InlineKeyboardButton("📞 Contact Us", callback_data="contact"),
            InlineKeyboardButton("📍 Location", callback_data="location"),
        ],
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
        "city",
        "state",
        "delivery_note",
        "delivery_method",
        "payment_choice",
        "pending_order",
    ]:
        context.user_data.pop(key, None)


def payment_label(index):
    try:
        bank, account_name, account_number = PAYMENT_ACCOUNTS[int(index)]
    except (ValueError, IndexError):
        return "Bank transfer"
    return f"{bank} — {account_name} — {account_number}"


# -------------------- USER COMMANDS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"🖤 *{NAME}*\n\n"
        f"*{TAGLINE}*\n\n"
        "Welcome! Discover beautiful hijabs, jilbabs, "
        "textiles and more.\n\n"
        "Choose an option:"
    )

    if update.message:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message or update.effective_message
    await target.reply_text(
        "🛍️ *Choose a collection:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "❓ *Help*\n\n"
        "Use /start to open the shop.\n"
        "Use /shop to browse products.\n"
        "Use /cart to view your cart.\n"
        "Use /orders to view your orders.\n"
        "Use /contact for contact details.\n\n"
        "To cancel an active checkout, send *cancel*.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_cart(update, context)


async def payment_done(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id_text: str):
    query = update.callback_query
    try:
        order_id = int(order_id_text)
    except ValueError:
        await query.answer("Invalid order number.", show_alert=True)
        return

    c = conn()
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        c.close()
        await query.answer("Order not found.", show_alert=True)
        return

    if row["user_id"] != update.effective_user.id:
        c.close()
        await query.answer("This order does not belong to you.", show_alert=True)
        return

    if row["payment_notified"]:
        c.close()
        await query.answer("We already received your payment notification.", show_alert=True)
        return

    c.execute("UPDATE orders SET payment_notified=1 WHERE id=?", (order_id,))
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"
    username = update.effective_user.username or "none"
    admin_text = (
        f"💳 *PAYMENT NOTIFICATION — #{order_number}*\n\n"
        f"👤 Customer: {row['customer_name']}\n"
        f"Username: @{username}\n"
        f"Telegram ID: `{row['user_id']}`\n"
        f"📞 Phone: {row['phone']}\n"
        f"🏦 Paid to: {row['payment_method'] or 'not specified'}\n"
        f"🚚 Delivery: {row['delivery_method'] or 'not specified'}\n"
        f"💰 Order Total: *{money(row['total'])}*\n"
        f"📦 Current Status: *{row['status']}*\n\n"
        "⚠️ Customer says payment has been made. Please verify the bank transaction before marking the order as PAID."
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode=ParseMode.MARKDOWN)
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
            f"#AD-{row['id']:05d} — {money(row['total'])} — {row['status']}"
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


# -------------------- CHECKOUT (Phase 2 + 3 + 4) --------------------

def payment_menu():
    buttons = []
    for i, (bank, account_name, account_number) in enumerate(PAYMENT_ACCOUNTS):
        buttons.append([
            InlineKeyboardButton(
                f"🏦 {bank} — {account_number}",
                callback_data=f"pay:{i}",
            )
        ])
    buttons.append([InlineKeyboardButton("🛒 Back to Cart", callback_data="cart")])
    return InlineKeyboardMarkup(buttons)


def delivery_menu():
    buttons = []
    for key, method in DELIVERY_METHODS.items():
        fee = delivery_fee(key)
        fee_text = "Free" if fee == 0 else money(fee)
        buttons.append([
            InlineKeyboardButton(
                f"{method['label']} — {fee_text}",
                callback_data=f"deliv:{key}",
            )
        ])
    buttons.append([InlineKeyboardButton("🛒 Back to Cart", callback_data="cart")])
    return InlineKeyboardMarkup(buttons)


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

    context.user_data.pop("checkout", None)

    await update.effective_message.reply_text(
        "💳 *Checkout — Step 1: Payment*\n\n"
        "Choose the account you'd like to pay into.\n"
        "(You'll get the full details after confirming your order.)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=payment_menu(),
    )


async def ask_delivery_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🚚 *Checkout — Step 2: Delivery*\n\n"
        "How would you like to receive your order?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=delivery_menu(),
    )


async def ask_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "name"
    await update.effective_message.reply_text(
        "👤 *Checkout — Step 3: Your details*\n\n"
        "Please send your *full name*.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "phone"
    await update.effective_message.reply_text(
        "📞 Now share your *phone number*.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Share Phone Number", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "address"
    await update.effective_message.reply_text(
        "📍 Send your *delivery address* (street, house number, landmark).",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )


async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "city"
    await update.effective_message.reply_text(
        "🏙️ Which *city / town*?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )


async def ask_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "state"
    await update.effective_message.reply_text(
        "🗺️ Which *state*?",
        parse_mode=ParseMode.MARKDOWN,
    )


async def ask_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkout"] = "note"
    await update.effective_message.reply_text(
        "📝 Any *additional delivery instructions*?\n\n"
        "Send them now, or type *none*.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )


async def show_order_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", {})
    name = context.user_data.get("customer_name") or update.effective_user.full_name
    phone = context.user_data.get("phone", "")
    address = context.user_data.get("address", "")
    city = context.user_data.get("city", "")
    state = context.user_data.get("state", "")
    note = context.user_data.get("delivery_note", "")
    method_key = context.user_data.get("delivery_method", "pickup")
    method = DELIVERY_METHODS.get(method_key, DELIVERY_METHODS["pickup"])
    fee = delivery_fee(method_key)
    pay_index = context.user_data.get("payment_choice", 0)

    lines, subtotal = cart_lines(cart)
    grand_total = None if subtotal is None else subtotal + fee

    context.user_data["customer_name"] = name
    context.user_data["pending_order"] = True

    detail_lines = [
        f"👤 Name: {name}",
        f"📞 Phone: {phone}",
    ]
    if method["needs_address"]:
        detail_lines.append(f"📍 Address: {address}")
        detail_lines.append(f"🏙️ City/Town: {city}")
        detail_lines.append(f"🗺️ State: {state}")
    else:
        detail_lines.append(f"📍 Pickup at: {ADDRESS}")
    detail_lines.append(f"📝 Instructions: {note or 'none'}")

    text = (
        "🧾 *ORDER SUMMARY*\n\n"
        "*Items:*\n"
        + "\n".join(lines)
        + "\n\n"
        + f"💳 Payment: {payment_label(pay_index)}\n"
        + f"🚚 Delivery: {method['short']}\n\n"
        + "*Customer details:*\n"
        + "\n".join(detail_lines)
        + "\n\n"
        + f"Subtotal: {money(subtotal)}\n"
        + f"Delivery fee: {'Free' if fee == 0 else money(fee)}\n"
        + f"💰 *TOTAL: {money(grand_total)}*"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm Order", callback_data="confirm_order"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_order"),
        ],
        [InlineKeyboardButton("🛒 Back to Cart", callback_data="cart")],
    ])

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


def payment_instructions(order_number, total, pay_index=None):
    lines = [
        "💳 *PAYMENT DETAILS*",
        "",
        f"Order Reference: *{order_number}*",
        f"Amount to Pay: *{money(total)}*",
        "",
        "Please transfer the exact amount and use your order number as the payment reference where possible.",
        "",
    ]

    accounts = list(enumerate(PAYMENT_ACCOUNTS, 1))
    if pay_index is not None:
        try:
            chosen = int(pay_index)
            accounts = [(chosen + 1, PAYMENT_ACCOUNTS[chosen])]
        except (ValueError, IndexError):
            pass

    for i, (bank, account_name, account_number) in accounts:
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
    customer_name = context.user_data.get("customer_name", user.full_name)
    phone = context.user_data.get("phone", "")
    address = context.user_data.get("address", "")
    city = context.user_data.get("city", "")
    state = context.user_data.get("state", "")
    note = context.user_data.get("delivery_note", "")
    method_key = context.user_data.get("delivery_method", "pickup")
    method = DELIVERY_METHODS.get(method_key, DELIVERY_METHODS["pickup"])
    fee = delivery_fee(method_key)
    pay_index = context.user_data.get("payment_choice", 0)
    pay_text = payment_label(pay_index)

    lines, subtotal = cart_lines(cart)
    items = "\n".join(lines)
    grand_total = (subtotal or 0) + fee

    if not method["needs_address"]:
        address = f"Pickup — {ADDRESS}"

    c = conn()
    cursor = c.execute(
        """
        INSERT INTO orders
        (user_id, username, customer_name, phone, address, items, total, status,
         payment_method, delivery_method, city, state, delivery_note, delivery_fee, subtotal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user.id,
            username,
            customer_name,
            phone,
            address,
            items,
            grand_total,
            "AWAITING_PAYMENT",
            pay_text,
            method["short"],
            city,
            state,
            note,
            fee,
            subtotal or 0,
        ),
    )

    order_id = cursor.lastrowid
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"

    admin_text = (
        f"🔔 *NEW ORDER #{order_number}*\n\n"
        f"👤 Customer: {customer_name}\n"
        f"Username: @{username or 'none'}\n"
        f"Telegram ID: `{user.id}`\n"
        f"📞 Phone: {phone}\n"
        f"📍 Address: {address}\n"
        f"🏙️ City: {city or '-'}\n"
        f"🗺️ State: {state or '-'}\n"
        f"📝 Instructions: {note or 'none'}\n"
        f"🚚 Delivery: {method['short']} ({'Free' if fee == 0 else money(fee)})\n"
        f"💳 Payment account: {pay_text}\n\n"
        f"*Items:*\n{items}\n\n"
        f"Subtotal: {money(subtotal)}\n"
        f"💰 *Total: {money(grand_total)}*\n"
        f"📦 Status: *AWAITING PAYMENT*"
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

    payment_text = payment_instructions(order_number, grand_total, pay_index)
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
        context.user_data.clear()
        await message.reply_text(
            "❌ Cancelled.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.reply_text(
            "Choose an option:",
            reply_markup=main_menu(),
        )
        return

    # Admin panel input flows (Phase 5)
    if is_admin(update) and context.user_data.get("admin_state"):
        await admin_text_flow(update, context, text)
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

    state = context.user_data.get("checkout")
    method_key = context.user_data.get("delivery_method", "pickup")
    needs_address = DELIVERY_METHODS.get(method_key, {}).get("needs_address", False)

    if state == "name":
        if not text:
            await message.reply_text("Please send your full name.")
            return
        context.user_data["customer_name"] = text
        await ask_phone(update, context)
        return

    if state == "phone":
        if message.contact:
            context.user_data["phone"] = message.contact.phone_number
        elif text:
            context.user_data["phone"] = text
        else:
            await message.reply_text("Please send a valid phone number.")
            return

        if needs_address:
            await ask_address(update, context)
        else:
            await ask_note(update, context)
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
            await message.reply_text("Please send your city or town.")
            return
        context.user_data["city"] = text
        await ask_state(update, context)
        return

    if state == "state":
        if not text:
            await message.reply_text("Please send your state.")
            return
        context.user_data["state"] = text
        await ask_note(update, context)
        return

    if state == "note":
        context.user_data["delivery_note"] = "" if lower in ("none", "no", "-") else text
        context.user_data.pop("checkout", None)
        await show_order_confirmation(update, context)
        return

    if is_admin(update):
        await message.reply_text(
            "👑 Type /admin to open the admin panel.",
        )


# -------------------- BUTTONS --------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("adm"):
        await admin_buttons(update, context, data)
        return

    if data in ("home", "start"):
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
            [InlineKeyboardButton("🛒 Add to Cart", callback_data=f"add:{product['id']}")],
            [InlineKeyboardButton("⚡ Order Now", callback_data=f"add:{product['id']}:now")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"cat:{product['category']}")],
        ])

        text = (
            f"*{product['name']}*\n\n"
            f"{product['description']}\n\n"
            f"💰 *{money(product['price'])}*"
        )

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
                    [InlineKeyboardButton("🛒 View Cart", callback_data="cart")],
                    [InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop")],
                ]),
            )

    elif data == "cart":
        await show_cart(update, context)

    elif data == "checkout":
        await checkout(update, context)

    elif data.startswith("pay:"):
        context.user_data["payment_choice"] = data.split(":", 1)[1]
        await query.edit_message_text(
            f"💳 Payment account selected:\n*{payment_label(context.user_data['payment_choice'])}*",
            parse_mode=ParseMode.MARKDOWN,
        )
        await ask_delivery_method(update, context)

    elif data.startswith("deliv:"):
        key = data.split(":", 1)[1]
        if key not in DELIVERY_METHODS:
            return
        context.user_data["delivery_method"] = key
        fee = delivery_fee(key)
        await query.edit_message_text(
            f"🚚 Delivery method selected:\n*{DELIVERY_METHODS[key]['short']}*"
            f" — {'Free' if fee == 0 else money(fee)}",
            parse_mode=ParseMode.MARKDOWN,
        )
        await ask_full_name(update, context)

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
            "❌ *Order cancelled.*\n\nYour cart has been cleared.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )

    elif data.startswith("payment_done:"):
        await payment_done(update, context, data.split(":", 1)[1])

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


# ============================================================
# PHASE 5 — PROFESSIONAL ADMIN PANEL
# ============================================================

def admin_panel_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="adm:products")],
        [
            InlineKeyboardButton("➕ Add", callback_data="adm:add"),
            InlineKeyboardButton("✏️ Edit", callback_data="adm:edit"),
            InlineKeyboardButton("🗑️ Delete", callback_data="adm:delete"),
        ],
        [InlineKeyboardButton("📋 View Orders", callback_data="adm:orders")],
        [InlineKeyboardButton("📊 Sales Summary", callback_data="adm:sales")],
        [InlineKeyboardButton("🚚 Delivery", callback_data="adm:delivery")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="adm:settings")],
    ])


ADMIN_PANEL_TEXT = (
    "👑 *ADMIN PANEL*\n\n"
    f"{NAME}\n"
    "Select an option below."
)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ You are not authorised to use this command.")
        return

    context.user_data.pop("admin_state", None)
    await update.effective_message.reply_text(
        ADMIN_PANEL_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_panel_markup(),
    )


def admin_back_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm:panel")]
    ])


def product_pick_markup(action):
    rows = get_products()
    buttons = [
        [InlineKeyboardButton(
            f"{r['name']} — {money(r['price'])}",
            callback_data=f"adm:{action}:{r['id']}",
        )]
        for r in rows
    ]
    buttons.append([InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm:panel")])
    return InlineKeyboardMarkup(buttons)


def sales_summary_text():
    c = conn()
    rows = c.execute("SELECT * FROM orders").fetchall()
    c.close()

    total_orders = len(rows)
    paid_rows = [r for r in rows if r["status"] in ("PAID", "PROCESSING", "OUT_FOR_DELIVERY", "DELIVERED")]
    revenue = sum(r["total"] or 0 for r in paid_rows)
    pending = [r for r in rows if r["status"] in ("PENDING", "AWAITING_PAYMENT")]
    cancelled = [r for r in rows if r["status"] == "CANCELLED"]
    delivered = [r for r in rows if r["status"] == "DELIVERED"]

    by_delivery = {}
    for r in rows:
        key = r["delivery_method"] or "Not set"
        by_delivery[key] = by_delivery.get(key, 0) + 1

    lines = [
        "📊 *SALES SUMMARY*",
        "",
        f"🧾 Total orders: *{total_orders}*",
        f"✅ Confirmed/paid: *{len(paid_rows)}*",
        f"⏳ Awaiting payment: *{len(pending)}*",
        f"📬 Delivered: *{len(delivered)}*",
        f"❌ Cancelled: *{len(cancelled)}*",
        "",
        f"💰 Confirmed revenue: *{money(revenue)}*",
        "",
        "🚚 *By delivery method*",
    ]
    for key, count in by_delivery.items():
        lines.append(f"• {key}: {count}")

    return "\n".join(lines)


def delivery_settings_text():
    lines = ["🚚 *DELIVERY SETTINGS*", ""]
    for key, method in DELIVERY_METHODS.items():
        fee = delivery_fee(key)
        lines.append(f"{method['label']}\nFee: *{'Free' if fee == 0 else money(fee)}*\n")
    lines.append("Tap a method below to change its fee.")
    return "\n".join(lines)


def delivery_settings_markup():
    buttons = [
        [InlineKeyboardButton(f"✏️ {m['short']}", callback_data=f"adm:fee:{k}")]
        for k, m in DELIVERY_METHODS.items()
    ]
    buttons.append([InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm:panel")])
    return InlineKeyboardMarkup(buttons)


async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query

    if not is_admin(update):
        await query.answer("⛔ Not authorised.", show_alert=True)
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "panel"
    arg = parts[2] if len(parts) > 2 else None

    if action == "panel":
        context.user_data.pop("admin_state", None)
        await query.edit_message_text(
            ADMIN_PANEL_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_panel_markup(),
        )

    elif action == "products":
        rows = get_products()
        if not rows:
            text = "📦 *Products*\n\nNo products yet."
        else:
            text = "📦 *Products*\n\n" + "\n".join(
                f"`{r['id']}`\n{r['name']} | {r['category']} | {money(r['price'])}"
                + ("\n📸 photo set" if r["photo_file_id"] else "\n📸 no photo")
                + "\n"
                for r in rows
            )
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_markup()
        )

    elif action == "add":
        context.user_data["admin_state"] = "add_product"
        await query.edit_message_text(
            "➕ *Add Product*\n\n"
            "Send the product in this format:\n\n"
            "`ID | Name | Category | Price | Description`\n\n"
            "Example:\n"
            "`black_abaya | Black Abaya | more | 18000 | Premium flowing abaya`\n\n"
            "Use `0` as the price for *price on request*.\n"
            "Categories: hijabs, jilbabs, textiles, more.\n\n"
            "Type *cancel* to stop.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )

    elif action == "edit":
        await query.edit_message_text(
            "✏️ *Edit Product*\n\nChoose the product to edit:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=product_pick_markup("editpick"),
        )

    elif action == "editpick":
        product = get_product(arg)
        if not product:
            await query.answer("Product not found.", show_alert=True)
            return
        context.user_data["admin_product"] = arg
        await query.edit_message_text(
            f"✏️ *Editing:* {product['name']}\n\nWhat do you want to change?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📛 Name", callback_data=f"adm:field:name")],
                [InlineKeyboardButton("💰 Price", callback_data=f"adm:field:price")],
                [InlineKeyboardButton("📝 Description", callback_data=f"adm:field:description")],
                [InlineKeyboardButton("🏷️ Category", callback_data=f"adm:field:category")],
                [InlineKeyboardButton("📸 Photo", callback_data=f"adm:field:photo")],
                [InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm:panel")],
            ]),
        )

    elif action == "field":
        product_id = context.user_data.get("admin_product")
        if not product_id:
            await query.answer("Pick a product first.", show_alert=True)
            return

        if arg == "photo":
            context.user_data["photo_target"] = product_id
            await query.edit_message_text(
                f"📸 Send the new photo for `{product_id}` now.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_back_markup(),
            )
            return

        context.user_data["admin_state"] = f"edit_{arg}"
        prompts = {
            "name": "Send the new *name*.",
            "price": "Send the new *price* as a number (e.g. 24000), or 0 for price on request.",
            "description": "Send the new *description*.",
            "category": "Send the new *category* (hijabs, jilbabs, textiles, more).",
        }
        await query.edit_message_text(
            prompts.get(arg, "Send the new value."),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )

    elif action == "delete":
        await query.edit_message_text(
            "🗑️ *Delete Product*\n\nChoose the product to remove from the shop:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=product_pick_markup("delpick"),
        )

    elif action == "delpick":
        product = get_product(arg)
        if not product:
            await query.answer("Product not found.", show_alert=True)
            return
        await query.edit_message_text(
            f"⚠️ Remove *{product['name']}* from the shop?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Yes, delete", callback_data=f"adm:delconfirm:{arg}")],
                [InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm:panel")],
            ]),
        )

    elif action == "delconfirm":
        c = conn()
        c.execute("UPDATE products SET active=0 WHERE id=?", (arg,))
        c.commit()
        c.close()
        await query.edit_message_text(
            "✅ Product removed from the shop.",
            reply_markup=admin_back_markup(),
        )

    elif action == "orders":
        await admin_orders_view(update, context)

    elif action == "sales":
        await query.edit_message_text(
            sales_summary_text(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )

    elif action == "delivery":
        await query.edit_message_text(
            delivery_settings_text(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=delivery_settings_markup(),
        )

    elif action == "fee":
        if arg not in DELIVERY_METHODS:
            return
        context.user_data["admin_state"] = f"fee_{arg}"
        await query.edit_message_text(
            f"🚚 Send the new fee for *{DELIVERY_METHODS[arg]['short']}* "
            "as a number (e.g. 1500). Use 0 for free.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )

    elif action == "settings":
        await query.edit_message_text(
            "⚙️ *SETTINGS*\n\n"
            f"🏪 Shop: {NAME}\n"
            f"📍 Address: {ADDRESS}\n"
            f"📞 {PHONE1} / {PHONE2}\n"
            f"🌐 {WEBSITE}\n"
            f"👑 Admin ID: `{ADMIN_ID}`\n\n"
            "*Payment accounts*\n"
            + "\n".join(
                f"{i}. {bank} — {acc_name} — {acc_no}"
                for i, (bank, acc_name, acc_no) in enumerate(PAYMENT_ACCOUNTS, 1)
            )
            + "\n\n"
            "*Order statuses*\n"
            + ", ".join(ORDER_STATUSES)
            + "\n\nUpdate an order with:\n`/status ORDER_ID STATUS`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )

    elif action == "status":
        # adm:status:<order_id>:<STATUS>
        try:
            order_id = int(parts[2])
            new_status = parts[3]
        except (IndexError, ValueError):
            return
        await apply_status(update, context, order_id, new_status, via_button=True)


async def admin_text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    state = context.user_data.get("admin_state", "")
    message = update.message

    if state == "add_product":
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 5:
            await message.reply_text(
                "Format:\n`ID | Name | Category | Price | Description`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        product_id, name, category, price, description = parts[:5]
        try:
            price_value = None if price.strip() == "0" else int(
                price.replace(",", "").replace("₦", "").strip()
            )
        except ValueError:
            await message.reply_text("Invalid price. Enter a number, e.g. 24000, or 0.")
            return

        c = conn()
        c.execute(
            """
            INSERT OR REPLACE INTO products
            (id, name, category, price, description, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (product_id, name, category.lower(), price_value, description),
        )
        c.commit()
        c.close()

        context.user_data.pop("admin_state", None)
        context.user_data["admin_product"] = product_id
        await message.reply_text(
            f"✅ Saved *{name}*.\n\nSend a photo now to attach it, or return to the panel.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )
        context.user_data["photo_target"] = product_id
        return

    if state.startswith("edit_"):
        field = state.split("_", 1)[1]
        product_id = context.user_data.get("admin_product")
        if not product_id:
            context.user_data.pop("admin_state", None)
            await message.reply_text("No product selected.", reply_markup=admin_back_markup())
            return

        value = text
        if field == "price":
            try:
                value = None if text.strip() == "0" else int(
                    text.replace(",", "").replace("₦", "").strip()
                )
            except ValueError:
                await message.reply_text("Invalid price. Send a number, e.g. 24000, or 0.")
                return
        if field == "category":
            value = text.lower().strip()

        c = conn()
        c.execute(f"UPDATE products SET {field}=? WHERE id=?", (value, product_id))
        c.commit()
        c.close()

        context.user_data.pop("admin_state", None)
        await message.reply_text(
            f"✅ {field.capitalize()} updated.",
            reply_markup=admin_back_markup(),
        )
        return

    if state.startswith("fee_"):
        key = state.split("_", 1)[1]
        try:
            fee = int(text.replace(",", "").replace("₦", "").strip())
        except ValueError:
            await message.reply_text("Send a number, e.g. 1500.")
            return

        set_setting(DELIVERY_METHODS[key]["fee_key"], fee)
        context.user_data.pop("admin_state", None)
        await message.reply_text(
            f"✅ {DELIVERY_METHODS[key]['short']} fee set to "
            f"{'Free' if fee == 0 else money(fee)}.",
            reply_markup=admin_back_markup(),
        )
        return

    context.user_data.pop("admin_state", None)
    await message.reply_text("Returning to the admin panel.", reply_markup=admin_back_markup())


async def admin_orders_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    c = conn()
    rows = c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 10").fetchall()
    c.close()

    if not rows:
        await query.edit_message_text(
            "📋 *Orders*\n\nNo orders yet.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )
        return

    await query.edit_message_text(
        "📋 *Latest orders*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_back_markup(),
    )

    for row in rows:
        await send_admin_order_card(context, query.message.chat_id, row)


async def send_admin_order_card(context, chat_id, row):
    order_number = f"AD-{row['id']:05d}"

    if row["payment_notified"]:
        payment_line = "💳 Payment notified" + (
            " — 🧾 receipt attached" if row["receipt_file_id"] else " — ⚠️ no receipt sent"
        )
    else:
        payment_line = "⏳ No payment notification yet"

    text = (
        f"📦 *Order #{order_number}*\n"
        f"👤 {row['customer_name']}\n"
        f"📞 {row['phone']}\n"
        f"🚚 {row['delivery_method'] or 'Not set'}\n"
        f"📍 {row['address']}\n"
        f"🏙️ {row['city'] or '-'} | 🗺️ {row['state'] or '-'}\n"
        f"📝 {row['delivery_note'] or 'none'}\n\n"
        f"*Items:*\n{row['items']}\n\n"
        f"Subtotal: {money(row['subtotal'])}\n"
        f"Delivery fee: {money(row['delivery_fee'] or 0)}\n"
        f"💰 Total: *{money(row['total'])}*\n"
        f"🏦 Paying to: {row['payment_method'] or '-'}\n"
        f"📦 Status: *{row['status']}*\n"
        f"{payment_line}\n"
        f"🕒 {row['created_at']}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ PAID", callback_data=f"adm:status:{row['id']}:PAID"),
            InlineKeyboardButton("⚙️ PROCESSING", callback_data=f"adm:status:{row['id']}:PROCESSING"),
        ],
        [
            InlineKeyboardButton("🚚 OUT FOR DELIVERY", callback_data=f"adm:status:{row['id']}:OUT_FOR_DELIVERY"),
        ],
        [
            InlineKeyboardButton("📬 DELIVERED", callback_data=f"adm:status:{row['id']}:DELIVERED"),
            InlineKeyboardButton("❌ CANCEL", callback_data=f"adm:status:{row['id']}:CANCELLED"),
        ],
    ])

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )

    if row["receipt_file_id"]:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=row["receipt_file_id"],
                caption=f"🧾 Receipt for #{order_number}",
            )
        except Exception:
            logger.exception("Could not resend stored receipt photo.")


async def apply_status(update, context, order_id, new_status, via_button=False):
    new_status = new_status.strip().upper()

    if new_status not in ORDER_STATUSES:
        await update.effective_message.reply_text(
            "Allowed statuses:\n" + "\n".join(ORDER_STATUSES)
        )
        return

    c = conn()
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        c.close()
        await update.effective_message.reply_text("Order not found.")
        return

    c.execute("UPDATE orders SET status=? WHERE id=?", (new_status, order_id))
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                f"📦 *Order #{order_number} Update*\n\n"
                f"Your order status is now:\n"
                f"*{new_status.replace('_', ' ')}*"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Could not notify customer about status change.")

    confirmation = f"✅ Order #{order_number} updated to *{new_status}*."

    if via_button:
        await update.callback_query.answer("Status updated.")
        await context.bot.send_message(
            chat_id=update.callback_query.message.chat_id,
            text=confirmation,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_markup(),
        )
    else:
        await update.effective_message.reply_text(
            confirmation, parse_mode=ParseMode.MARKDOWN
        )


# -------------------- LEGACY ADMIN COMMANDS (kept) --------------------

async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    rows = get_products()

    if not rows:
        await update.message.reply_text("No products.")
        return

    text = "🛠️ *Products*\n\n"
    for row in rows:
        text += f"`{row['id']}` | {row['name']} | {row['category']} | {money(row['price'])}\n"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


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
        price_value = None if price == "0" else int(
            price.replace(",", "").replace("₦", "").strip()
        )
    except ValueError:
        await update.message.reply_text("Invalid price. Enter a number, e.g. 24000, or 0.")
        return

    c = conn()
    c.execute(
        """
        INSERT OR REPLACE INTO products
        (id, name, category, price, description, active)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (product_id, name, category.lower(), price_value, description),
    )
    c.commit()
    c.close()

    await update.message.reply_text(f"✅ Saved: {name}")


async def deleteproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    product_id = update.message.text.partition(" ")[2].strip()

    if not product_id:
        await update.message.reply_text("Use: /deleteproduct PRODUCT_ID")
        return

    c = conn()
    c.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))
    c.commit()
    c.close()

    await update.message.reply_text("✅ Product removed from the shop.")


async def setphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    product_id = update.message.text.partition(" ")[2].strip()

    if not product_id:
        await update.message.reply_text("Use: /setphoto PRODUCT_ID")
        return

    if not get_product(product_id):
        await update.message.reply_text("Product ID not found.")
        return

    context.user_data["photo_target"] = product_id

    await update.message.reply_text(
        f"📸 Now send the photo for `{product_id}`.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin flow: setting a product photo.
    if is_admin(update) and context.user_data.get("photo_target"):
        product_id = context.user_data["photo_target"]
        file_id = update.message.photo[-1].file_id

        c = conn()
        c.execute("UPDATE products SET photo_file_id=? WHERE id=?", (file_id, product_id))
        c.commit()
        c.close()

        context.user_data.pop("photo_target", None)

        await update.message.reply_text(
            "✅ Product photo saved.",
            reply_markup=admin_back_markup(),
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

        c.execute("UPDATE orders SET receipt_file_id=? WHERE id=?", (file_id, order_id))
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
                    f"Customer: {row['customer_name']}\n"
                    f"Delivery: {row['delivery_method'] or '-'}\n"
                    f"Total: {money(row['total'])}\n\n"
                    "Verify against your bank record, then use "
                    f"/status {order_id} PAID"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            logger.exception("Could not forward receipt photo to admin.")

        await update.message.reply_text(
            f"✅ Receipt received for #{order_number}. We'll confirm your order shortly.",
            reply_markup=main_menu(),
        )
        return

    if is_admin(update):
        await update.message.reply_text("Use /admin → Edit Product → Photo first.")


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    c = conn()
    rows = c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 20").fetchall()
    c.close()

    if not rows:
        await update.message.reply_text("No orders yet.")
        return

    for row in rows:
        await send_admin_order_card(context, update.effective_chat.id, row)


async def sales_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        sales_summary_text(),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_back_markup(),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    raw = update.message.text.partition(" ")[2].strip()
    parts = raw.split(maxsplit=1)

    if len(parts) != 2:
        await update.message.reply_text(
            "Format:\n/status ORDER_ID STATUS\n\nExample:\n/status 1 PAID"
        )
        return

    try:
        order_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("Invalid order ID.")
        return

    await apply_status(update, context, order_id, parts[1])


# -------------------- HEALTH SERVER --------------------

app_flask = Flask(__name__)


@app_flask.get("/")
def health():
    return {
        "status": "ok",
        "service": NAME,
        "phase": "5",
    }


def health_server():
    app_flask.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False,
    )


# -------------------- MAIN --------------------

async def main():
    init_db()

    Thread(target=health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    commands = [
        ("start", start),
        ("help", help_cmd),
        ("shop", shop),
        ("cart", cart_cmd),
        ("orders", orders_cmd),
        ("contact", contact_cmd),
        ("admin", admin_cmd),
        ("admin_products", admin_products),
        ("addproduct", addproduct),
        ("deleteproduct", deleteproduct),
        ("setphoto", setphoto),
        ("admin_orders", admin_orders),
        ("sales", sales_cmd),
        ("status", status_command),
    ]

    for command, handler in commands:
        app.add_handler(CommandHandler(command, handler))

    app.add_handler(CallbackQueryHandler(buttons))

    app.add_handler(MessageHandler(filters.PHOTO, photo))

    app.add_handler(
        MessageHandler(
            filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
            text_handler,
        )
    )

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    logger.info("AD Fashion Hijabs & More bot is running (Phase 5).")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
