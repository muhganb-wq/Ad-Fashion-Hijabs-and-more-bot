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
# AD FASHION HIJABS & MORE — TELEGRAM SHOP BOT — PHASE 3 PAYMENT
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
        "Thank you. We will verify your payment and update your order status.\n\n"
        "Please keep your transfer receipt until your order is confirmed.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
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


# -------------------- CHECKOUT PHASE 2 --------------------

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

    context.user_data["checkout"] = "phone"

    await update.effective_message.reply_text(
        "🧾 *Checkout — Step 1 of 3*\n\n"
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
        "📍 *Checkout — Step 2 of 3*\n\n"
        "Now send your complete delivery address.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )


async def show_order_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cart = context.user_data.get("cart", {})
    phone = context.user_data.get("phone", "")
    address = context.user_data.get("address", "")
    name = update.effective_user.full_name

    lines, total = cart_lines(cart)

    context.user_data["customer_name"] = name
    context.user_data["pending_order"] = True

    text = (
        "🧾 *Checkout — Step 3 of 3*\n\n"
        "*Please confirm your order:*\n\n"
        f"👤 Customer: {name}\n"
        f"📞 Phone: {phone}\n"
        f"📍 Address: {address}\n\n"
        "*Items:*\n"
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
    address = context.user_data.get("address", "")

    lines, total = cart_lines(cart)
    items = "\n".join(lines)

    c = conn()

    cursor = c.execute(
        """
        INSERT INTO orders
        (user_id, username, customer_name, phone, address, items, total, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        f"📍 Address: {address}\n\n"
        f"*Items:*\n{items}\n\n"
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
        [InlineKeyboardButton("🏠 Main Menu", callback_data="start")],
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
            "❌ Checkout cancelled.",
            reply_markup=main_menu(),
        )
        return

    state = context.user_data.get("checkout")

    if state == "phone":
        if message.contact:
            context.user_data["phone"] = message.contact.phone_number
        else:
            context.user_data["phone"] = text

        await ask_address(update, context)
        return

    if state == "address":
        if not text:
            await message.reply_text("Please send a valid delivery address.")
            return

        context.user_data["address"] = text
        await show_order_confirmation(update, context)
        return

    if is_admin(update):
        await message.reply_text(
            "👨‍💼 *Admin commands:*\n\n"
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
    if not is_admin(update):
        return

    product_id = context.user_data.get("photo_target")

    if not product_id:
        await update.message.reply_text(
            "Use /setphoto PRODUCT_ID first."
        )
        return

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

        text = (
            f"📦 *Order #{order_number}*\n"
            f"Customer: {row['customer_name']}\n"
            f"Phone: {row['phone']}\n"
            f"Address: {row['address']}\n\n"
            f"Items:\n{row['items']}\n\n"
            f"Total: {money(row['total'])}\n"
            f"Status: *{row['status']}*\n"
            f"Created: {row['created_at']}"
        )

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
        )


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

    allowed = {
        "PENDING",
        "AWAITING_PAYMENT",
        "PAID",
        "PROCESSING",
        "OUT_FOR_DELIVERY",
        "DELIVERED",
        "CANCELLED",
    }

    if new_status not in allowed:
        await update.message.reply_text(
            "Allowed statuses:\n"
            "PENDING\n"
            "AWAITING_PAYMENT\n"
            "PAID\n"
            "PROCESSING\n"
            "OUT_FOR_DELIVERY\n"
            "DELIVERED\n"
            "CANCELLED"
        )
        return

    c = conn()
    row = c.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
    ).fetchone()

    if not row:
        c.close()
        await update.message.reply_text("Order not found.")
        return

    c.execute(
        "UPDATE orders SET status=? WHERE id=?",
        (new_status, order_id),
    )
    c.commit()
    c.close()

    order_number = f"AD-{order_id:05d}"

    # Notify the customer about the status change.
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

    await update.message.reply_text(
        f"✅ Order #{order_number} updated to *{new_status}*.",
        parse_mode=ParseMode.MARKDOWN,
    )


# -------------------- HEALTH SERVER --------------------

app_flask = Flask(__name__)


@app_flask.get("/")
def health():
    return {
        "status": "ok",
        "service": NAME,
        "phase": "2",
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
