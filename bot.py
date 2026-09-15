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
# PHASES 1–5: Shop, Cart, Checkout, Payment, Delivery, Admin Panel
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

    # Phase 3 payment fields. Safe for existing databases.
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(orders)").fetchall()}
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
        "delivery_method",
        "city",
        "state",
        "delivery_notes",
        "pending_order",
    ]:
        context.user_data.pop(key, None)


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

    c.execute("UPDATE orders SET payment_notified=1, payment_method=? WHERE id=?", ("CUSTOMER_PAYMENT_NOTIFICATION", order_id))
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
            f"#{row['id']} — {money(row['total'])} — {row['status']}"
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
        delivery_lines.append(f"📍 Address: {address}")
        delivery_lines.append(f"🏙️ City/Town: {city}")
        if state:
            delivery_lines.append(f"🗺️ State: {state}")
    if notes and notes.lower() != "none":
        delivery_lines.append(f"📝 Notes: {notes}")

    text = (
        "🧾 *Confirm your order*\n\n"
        f"👤 Customer: {name}\n"
        f"📞 Phone: {phone}\n"
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
         status, delivery_method, city, state, delivery_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )

    order_id = cursor.lastrowid
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"

    delivery_summary = [f"🚚 Delivery: {DELIVERY_LABELS.get(method, method)}"]
    if method == "pickup":
        delivery_summary.append(f"📍 Pickup point: {ADDRESS}")
    else:
        delivery_summary.append(f"📍 Address: {address}")
        delivery_summary.append(f"🏙️ City/Town: {city}")
        if state:
            delivery_summary.append(f"🗺️ State: {state}")
    if notes:
        delivery_summary.append(f"📝 Notes: {notes}")

    admin_text = (
        f"🔔 *NEW ORDER #{order_number}*\n\n"
        f"👤 Customer: {customer_name}\n"
        f"Username: @{username or 'none'}\n"
        f"Telegram ID: `{user.id}`\n"
        f"📞 Phone: {phone}\n"
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
        await handle_admin_add_step(message, context, admin_add, text)
        return

    # ---- Admin panel: guided "edit product" flow ----
    admin_edit = context.user_data.get("admin_edit")
    if admin_edit and is_admin(update):
        await handle_admin_edit_step(message, context, admin_edit, text)
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
            await query.answer("Unknown delivery method.", show_alert=True)
            return
        await query.edit_message_text(
            f"🚚 *{DELIVERY_LABELS[method]}* selected.",
            parse_mode=ParseMode.MARKDOWN,
        )
        await start_delivery_details(update, context, method)

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
            await query.answer("Admins only.", show_alert=True)
            return
        await handle_admin_callback(update, context, data)


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
                    f"Customer: {row['customer_name']}\n"
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
            f"Customer: {row['customer_name']}\n"
            f"Phone: {row['phone']}\n"
            f"Address: {row['address']}\n"
            f"{delivery_line}\n\n"
            f"Items:\n{row['items']}\n\n"
            f"Total: {money(row['total'])}\n"
            f"Status: *{row['status']}*\n"
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

    c.execute(
        "UPDATE orders SET status=? WHERE id=?",
        (new_status, order_id),
    )
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
        f"✅ Order #{order_number} updated to *{new_status}*.",
        parse_mode=ParseMode.MARKDOWN,
    )


# -------------------- ADMIN PANEL (PHASE 5) --------------------

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="adm:products")],
        [InlineKeyboardButton("📋 View Orders", callback_data="adm:vieworders")],
        [InlineKeyboardButton("📊 Sales Summary", callback_data="adm:sales")],
        [InlineKeyboardButton("🚚 Delivery", callback_data="adm:delivery")],
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
            await query.answer("Product not found.", show_alert=True)
            return
        text = (
            f"*{product['name']}*\n"
            f"ID: `{product['id']}`\n"
            f"Category: {product['category']}\n"
            f"Price: {money(product['price'])}\n\n"
            f"{product['description']}"
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
            "➕ *Add Product — Step 1 of 5*\n\n"
            "Send a short unique ID for the product (e.g. `navy_hijab`).\n\n"
            "Type *cancel* anytime to stop.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "adm:editproduct":
        rows = get_products()
        if not rows:
            await query.answer("No products to edit.", show_alert=True)
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
            await query.answer("Product not found.", show_alert=True)
            return
        buttons = [
            [InlineKeyboardButton("Name", callback_data=f"adm:editf:{product_id}:name")],
            [InlineKeyboardButton("Category", callback_data=f"adm:editf:{product_id}:category")],
            [InlineKeyboardButton("Price", callback_data=f"adm:editf:{product_id}:price")],
            [InlineKeyboardButton("Description", callback_data=f"adm:editf:{product_id}:description")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:editproduct")],
        ]
        await query.edit_message_text(
            f"✏️ *Editing {product['name']}*\n\nWhich field?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("adm:editf:"):
        _, _, product_id, field = data.split(":", 3)
        product = get_product(product_id)
        if not product:
            await query.answer("Product not found.", show_alert=True)
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
            await query.answer("No products to delete.", show_alert=True)
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
            await query.answer("Product not found.", show_alert=True)
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
                    f"#{order_number} — {row['customer_name']} — "
                    f"{money(row['total'])} — *{row['status']}*"
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
            lines.append(f"• {row['status']}: {row['n']}")

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
                    f"#{order_number} — {row['customer_name']} — "
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
            await query.answer("Invalid order.", show_alert=True)
            return

        row = await update_order_status(context, order_id, new_status)
        if not row:
            await query.answer("Order not found.", show_alert=True)
            return

        await query.answer(f"Order #{order_id:05d} → {new_status}")
        await handle_admin_callback(update, context, "adm:delivery")
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

    await query.answer("Unknown admin action.", show_alert=True)


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
        await message.reply_text("➕ *Step 2 of 5*\n\nSend the product name.", parse_mode=ParseMode.MARKDOWN)
        return

    if step == "name":
        if not text.strip():
            await message.reply_text("Please send a valid name.")
            return
        data["name"] = text.strip()
        admin_add["step"] = "category"
        await message.reply_text(
            "➕ *Step 3 of 5*\n\nSend the category (e.g. hijabs, jilbabs, textiles, more).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "category":
        if not text.strip():
            await message.reply_text("Please send a valid category.")
            return
        data["category"] = text.strip().lower()
        admin_add["step"] = "price"
        await message.reply_text(
            "➕ *Step 4 of 5*\n\nSend the price as a number (e.g. 24000), or 0 for price-on-request.",
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
            "➕ *Step 5 of 5*\n\nSend a short description.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if step == "description":
        data["description"] = text.strip()

        c = conn()
        c.execute(
            """
            INSERT OR REPLACE INTO products
            (id, name, category, price, description, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (data["id"], data["name"], data["category"], data["price"], data["description"]),
        )
        c.commit()
        c.close()

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
    ]

    for command, handler in commands:
        app.add_handler(
            CommandHandler(command, handler)
        )

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

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES
    )

    logger.info("AD Fashion Hijabs & More bot is running.")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
