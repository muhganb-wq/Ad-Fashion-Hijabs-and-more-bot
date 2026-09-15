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
# AD FASHION HIJABS & MORE — TELEGRAM SHOP BOT — PHASE 3
# PAYMENT SYSTEM
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

# ============================================================
# PAYMENT ACCOUNTS
# ============================================================

PAYMENT_ACCOUNTS = [
    (
        "OPAY",
        "MUHAMMAD MUHAMMAD ADAMU",
        "7011927516",
    ),
    (
        "OPAY",
        "MUHAMMAD ADAMU",
        "9136114700",
    ),
    (
        "POLARIS BANK",
        "MUHAMMAD ADAMU",
        "3095751555",
    ),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()

    connection.execute("""
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

    connection.execute("""
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            payment_notified INTEGER DEFAULT 0,
            payment_method TEXT DEFAULT ''
        )
    """)

    existing_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(orders)"
        ).fetchall()
    }

    if "payment_notified" not in existing_columns:
        connection.execute(
            "ALTER TABLE orders ADD COLUMN payment_notified INTEGER DEFAULT 0"
        )

    if "payment_method" not in existing_columns:
        connection.execute(
            "ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT ''"
        )

    starter_products = [
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

    for product_data in starter_products:
        connection.execute(
            """
            INSERT OR IGNORE INTO products
            (id, name, category, price, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            product_data,
        )

    connection.commit()
    connection.close()


def money(value):
    if value is None:
        return "Price on request"

    return f"₦{value:,.0f}"


def get_product(product_id):
    connection = db()

    row = connection.execute(
        "SELECT * FROM products WHERE id=?",
        (product_id,),
    ).fetchone()

    connection.close()

    return row


def get_products(category=None):
    connection = db()

    if category:
        rows = connection.execute(
            """
            SELECT * FROM products
            WHERE active=1 AND category=?
            ORDER BY name
            """,
            (category,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM products
            WHERE active=1
            ORDER BY name
            """
        ).fetchall()

    connection.close()

    return rows


# ============================================================
# HELPERS
# ============================================================

def is_admin(update):
    user = update.effective_user

    return bool(
        user and user.id == ADMIN_ID
    )


def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛍️ Shop Products",
                callback_data="shop",
            )
        ],
        [
            InlineKeyboardButton(
                "🧕 Hijabs",
                callback_data="cat:hijabs",
            ),
            InlineKeyboardButton(
                "👗 Jilbabs",
                callback_data="cat:jilbabs",
            ),
        ],
        [
            InlineKeyboardButton(
                "🧵 Textiles",
                callback_data="cat:textiles",
            ),
            InlineKeyboardButton(
                "✨ More",
                callback_data="cat:more",
            ),
        ],
        [
            InlineKeyboardButton(
                "🛒 My Cart",
                callback_data="cart",
            ),
            InlineKeyboardButton(
                "📦 My Orders",
                callback_data="orders",
            ),
        ],
        [
            InlineKeyboardButton(
                "📞 Contact Us",
                callback_data="contact",
            ),
            InlineKeyboardButton(
                "📍 Location",
                callback_data="location",
            ),
        ],
        [
            InlineKeyboardButton(
                "🌐 Visit Website",
                url=WEBSITE,
            )
        ],
    ])


def category_menu(category):
    rows = []

    for product in get_products(category):
        rows.append([
            InlineKeyboardButton(
                f"{product['name']} — {money(product['price'])}",
                callback_data=f"product:{product['id']}",
            )
        ])

    if not rows:
        rows.append([
            InlineKeyboardButton(
                "No products available yet",
                callback_data="shop",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🏠 Main Menu",
            callback_data="home",
        )
    ])

    return InlineKeyboardMarkup(rows)


def cart_data(context):
    cart = context.user_data.get("cart", {})

    lines = []
    total = 0
    unknown_price = False

    for product_id, quantity in cart.items():

        product = get_product(product_id)

        if not product:
            continue

        if product["price"] is None:
            subtotal = "Price on request"
            unknown_price = True
        else:
            subtotal_value = product["price"] * quantity
            subtotal = money(subtotal_value)
            total += subtotal_value

        lines.append(
            f"• {product['name']} × {quantity} — {subtotal}"
        )

    if unknown_price:
        return lines, None

    return lines, total


# ============================================================
# USER COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.effective_message.reply_text(
        f"🖤 <b>{NAME}</b>\n\n"
        f"<b>{TAGLINE}</b>\n\n"
        "Welcome! Discover beautiful hijabs, "
        "jilbabs, textiles and more.\n\n"
        "Choose an option:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.effective_message.reply_text(
        "🛍️ <b>Choose a collection:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.effective_message.reply_text(
        "❓ <b>Help</b>\n\n"
        "Use /start to open the shop.\n"
        "Use /shop to browse products.\n"
        "Use /cart to view your cart.\n"
        "Use /orders to view your orders.\n"
        "Use /contact for contact details.\n\n"
        "To cancel checkout, send <b>cancel</b>.",
        parse_mode=ParseMode.HTML,
    )


async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.effective_message.reply_text(
        f"📞 <b>{NAME}</b>\n\n"
        f"{PHONE1}\n"
        f"{PHONE2}\n\n"
        f"WhatsApp: https://wa.me/{WHATSAPP}\n"
        f"Website: {WEBSITE}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):

    connection = db()

    rows = connection.execute(
        """
        SELECT * FROM orders
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (update.effective_user.id,),
    ).fetchall()

    connection.close()

    if rows:
        order_text = "\n".join(
            f"#{row['id']} — "
            f"{money(row['total'])} — "
            f"{row['status']}"
            for row in rows
        )
    else:
        order_text = "You have no orders yet."

    await update.effective_message.reply_text(
        "📦 <b>Your Recent Orders</b>\n\n"
        + order_text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


# ============================================================
# CART
# ============================================================

async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):

    lines, total = cart_data(context)

    if not lines:

        await update.effective_message.reply_text(
            "🛒 <b>Your cart is empty.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🧾 Checkout",
                callback_data="checkout",
            )
        ],
        [
            InlineKeyboardButton(
                "🗑️ Clear Cart",
                callback_data="clear",
            )
        ],
        [
            InlineKeyboardButton(
                "🛍️ Continue Shopping",
                callback_data="shop",
            )
        ],
    ])

    await update.effective_message.reply_text(
        "🛒 <b>Your Cart</b>\n\n"
        + "\n".join(lines)
        + f"\n\n<b>Total: {money(total)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ============================================================
# CHECKOUT
# ============================================================

async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.user_data.get("cart"):

        await update.effective_message.reply_text(
            "🛒 Your cart is empty.",
            reply_markup=main_menu(),
        )

        return

    context.user_data["checkout"] = "phone"

    keyboard = ReplyKeyboardMarkup(
        [[
            KeyboardButton(
                "📱 Share Phone Number",
                request_contact=True,
            )
        ]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await update.effective_message.reply_text(
        "🧾 <b>Checkout — Step 1 of 3</b>\n\n"
        "Please share your phone number.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def address_step(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["checkout"] = "address"

    await update.message.reply_text(
        "📍 <b>Checkout — Step 2 of 3</b>\n\n"
        "Send your complete delivery address.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )


async def confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):

    lines, total = cart_data(context)

    context.user_data["customer_name"] = (
        update.effective_user.full_name
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
        [
            InlineKeyboardButton(
                "🛒 Back to Cart",
                callback_data="cart",
            )
        ],
    ])

    await update.message.reply_text(
        "🧾 <b>Checkout — Step 3 of 3</b>\n\n"
        "<b>Please confirm your order:</b>\n\n"
        f"👤 Customer: {update.effective_user.full_name}\n"
        f"📞 Phone: {context.user_data.get('phone', '')}\n"
        f"📍 Address: {context.user_data.get('address', '')}\n\n"
        "<b>Items:</b>\n"
        + "\n".join(lines)
        + f"\n\n💰 <b>Total: {money(total)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ============================================================
# PAYMENT
# ============================================================

def payment_instructions(order_number, total):

    text = (
        "💳 <b>PAYMENT OPTIONS</b>\n\n"
        f"Order Reference: <b>{order_number}</b>\n"
        f"Amount to Pay: <b>{money(total)}</b>\n\n"
        "Please transfer the exact amount to any "
        "account below.\n"
        "Use your order number as the payment reference "
        "where possible.\n\n"
    )

    for number, account in enumerate(
        PAYMENT_ACCOUNTS,
        start=1,
    ):

        bank, account_name, account_number = account

        text += (
            f"<b>Option {number}</b>\n"
            f"🏦 Bank: <b>{bank}</b>\n"
            f"👤 Account Name: <b>{account_name}</b>\n"
            f"🔢 Account Number: <b>{account_number}</b>\n\n"
        )

    text += (
        "After making payment, tap "
        "<b>💳 I've Made Payment</b> below."
    )

    return text


async def create_order(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.user_data.get("cart"):

        await update.effective_message.reply_text(
            "🛒 Your cart is empty.",
            reply_markup=main_menu(),
        )

        return

    lines, total = cart_data(context)

    user = update.effective_user

    items = "\n".join(lines)

    phone = context.user_data.get(
        "phone",
        "",
    )

    address = context.user_data.get(
        "address",
        "",
    )

    connection = db()

    cursor = connection.execute(
        """
        INSERT INTO orders
        (
            user_id,
            username,
            customer_name,
            phone,
            address,
            items,
            total,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user.id,
            user.username or "",
            user.full_name,
            phone,
            address,
            items,
            total or 0,
            "AWAITING_PAYMENT",
        ),
    )

    order_id = cursor.lastrowid

    connection.commit()
    connection.close()

    order_number = f"AD-{order_id:05d}"

    admin_message = (
        f"🔔 <b>NEW ORDER #{order_number}</b>\n\n"
        f"👤 Customer: {user.full_name}\n"
        f"Username: @{user.username or 'none'}\n"
        f"Telegram ID: <code>{user.id}</code>\n"
        f"📞 Phone: {phone}\n"
        f"📍 Address: {address}\n\n"
        f"<b>Items:</b>\n{items}\n\n"
        f"💰 <b>Total: {money(total)}</b>\n"
        f"📦 Status: <b>AWAITING PAYMENT</b>"
    )

    try:

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            parse_mode=ParseMode.HTML,
        )

    except Exception:

        logger.exception(
            "Could not send order notification."
        )

    context.user_data.clear()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💳 I've Made Payment",
                callback_data=f"payment_done:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Main Menu",
                callback_data="home",
            )
        ],
    ])

    await update.effective_message.reply_text(
        f"✅ <b>Order #{order_number} received!</b>\n\n"
        "Your order has been created successfully.\n\n"
        + payment_instructions(
            order_number,
            total,
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def payment_done(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    order_id_text,
):

    query = update.callback_query

    try:
        order_id = int(order_id_text)

    except ValueError:

        await query.answer(
            "Invalid order number.",
            show_alert=True,
        )

        return

    connection = db()

    row = connection.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
    ).fetchone()

    if not row:

        connection.close()

        await query.answer(
            "Order not found.",
            show_alert=True,
        )

        return

    if row["user_id"] != update.effective_user.id:

        connection.close()

        await query.answer(
            "This order does not belong to you.",
            show_alert=True,
        )

        return

    if row["payment_notified"]:

        connection.close()

        await query.answer(
            "Payment notification already received.",
            show_alert=True,
        )

        return

    connection.execute(
        """
        UPDATE orders
        SET payment_notified=1,
            payment_method='CUSTOMER_PAYMENT_NOTIFICATION'
        WHERE id=?
        """,
        (order_id,),
    )

    connection.commit()
    connection.close()

    order_number = f"AD-{order_id:05d}"

    admin_message = (
        f"💳 <b>PAYMENT NOTIFICATION — #{order_number}</b>\n\n"
        f"👤 Customer: {row['customer_name']}\n"
        f"Username: @{update.effective_user.username or 'none'}\n"
        f"Telegram ID: <code>{row['user_id']}</code>\n"
        f"📞 Phone: {row['phone']}\n"
        f"💰 Total: <b>{money(row['total'])}</b>\n"
        f"📦 Status: <b>{row['status']}</b>\n\n"
        "⚠️ Customer says payment was made.\n"
        "Verify the transfer before marking the order as PAID."
    )

    try:

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            parse_mode=ParseMode.HTML,
        )

    except Exception:

        logger.exception(
            "Could not send payment notification."
        )

    await query.edit_message_text(
        f"✅ <b>Payment notification received "
        f"for #{order_number}</b>\n\n"
        "We will verify your payment and update "
        "your order status.\n\n"
        "Please keep your transfer receipt.",
        parse_mode=ParseMode.HTML,
       
