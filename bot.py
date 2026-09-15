import os, sqlite3, asyncio, logging
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6921853187"))
PORT = int(os.environ.get("PORT", "10000"))
NAME = "AD FASHION HIJABS & MORE"
TAGLINE = "MODESTY • ELEGANCE • QUALITY"
PHONE1, PHONE2 = "09136114700", "07011927516"
WEBSITE = "https://adfashionhijabs.netlify.app/"
ADDRESS = "Bauchi Central Market, Bauchi, Nigeria"
WHATSAPP = "2349136114700"
DB = "shop.db"
logging.basicConfig(level=logging.INFO)

def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init_db():
    c=conn()
    c.execute("CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,name TEXT,category TEXT,price INTEGER,description TEXT,photo_file_id TEXT,active INTEGER DEFAULT 1)")
    c.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,username TEXT,customer_name TEXT,phone TEXT,address TEXT,items TEXT,total INTEGER,status TEXT DEFAULT 'NEW',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    starter=[("royal_blue_hijab","Royal Blue Hijab","hijabs",None,"Elegant royal blue hijab. Price on request."),
             ("ismat_jilbab","Ismat Jilbab","jilbabs",24000,"Free-size maxi jilbab. Elegant, comfortable and modest.")]
    for p in starter: c.execute("INSERT OR IGNORE INTO products(id,name,category,price,description) VALUES(?,?,?,?,?)",p)
    c.commit(); c.close()
def money(n): return "Price on request" if n is None else f"₦{n:,.0f}"
def get_products(cat=None):
    c=conn()
    q="SELECT * FROM products WHERE active=1"+(" AND category=?" if cat else "")+" ORDER BY name"
    r=c.execute(q,(cat,) if cat else ()).fetchall(); c.close(); return r
def get_product(pid):
    c=conn(); r=c.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone(); c.close(); return r
def admin(u): return u.effective_user and u.effective_user.id==ADMIN_ID

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Shop Products",callback_data="shop")],
        [InlineKeyboardButton("🧕 Hijabs",callback_data="cat:hijabs"),InlineKeyboardButton("👗 Jilbabs",callback_data="cat:jilbabs")],
        [InlineKeyboardButton("🧵 Textiles",callback_data="cat:textiles"),InlineKeyboardButton("✨ More",callback_data="cat:more")],
        [InlineKeyboardButton("🛒 My Cart",callback_data="cart"),InlineKeyboardButton("📦 My Orders",callback_data="orders")],
        [InlineKeyboardButton("📞 Contact Us",callback_data="contact"),InlineKeyboardButton("📍 Location",callback_data="location")],
        [InlineKeyboardButton("🌐 Visit Website",url=WEBSITE)]
    ])
def cat_menu(cat):
    rows=get_products(cat); b=[[InlineKeyboardButton(f"{r['name']} — {money(r['price'])}",callback_data=f"product:{r['id']}")] for r in rows]
    b.append([InlineKeyboardButton("⬅️ Main Menu",callback_data="home")]); return InlineKeyboardMarkup(b)

async def start(u,ct):
    await u.message.reply_text(f"🖤 *{NAME}*\n\n*{TAGLINE}*\n\nWelcome! Discover beautiful hijabs, jilbabs, textiles and more.\n\nChoose an option:",parse_mode=ParseMode.MARKDOWN,reply_markup=menu())
async def shop(u,ct): await u.message.reply_text("🛍️ Choose a collection:",reply_markup=menu())
async def help_cmd(u,ct): await u.message.reply_text("Use /start to open the shop.\nUse /shop to browse.\nUse /cart for your cart.\nUse /contact for contact details.")
async def cart_cmd(u,ct): await show_cart(u,ct)
async def orders_cmd(u,ct):
    c=conn(); rows=c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(u.effective_user.id,)).fetchall(); c.close()
    text="📦 You have no orders yet." if not rows else "📦 *Your recent orders*\n\n"+"\n".join(f"#{r['id']} — {money(r['total'])} — {r['status']}" for r in rows)
    if u.callback_query: await u.callback_query.edit_message_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=menu())
    else: await u.message.reply_text(text,parse_mode=ParseMode.MARKDOWN)
async def contact_cmd(u,ct):
    await u.message.reply_text(f"📞 *{NAME}*\n\n{PHONE1}\n{PHONE2}\n\nWhatsApp: https://wa.me/{WHATSAPP}\nWebsite: {WEBSITE}",parse_mode=ParseMode.MARKDOWN)

async def buttons(u,ct):
    q=u.callback_query; await q.answer(); d=q.data
    if d=="home": await q.edit_message_text(f"🖤 *{NAME}*\n\n*{TAGLINE}*\n\nChoose an option:",parse_mode=ParseMode.MARKDOWN,reply_markup=menu())
    elif d=="shop": await q.edit_message_text("🛍️ Choose a collection:",reply_markup=menu())
    elif d.startswith("cat:"):
        cat=d.split(":")[1]; labels={"hijabs":"🧕 Hijabs","jilbabs":"👗 Jilbabs","textiles":"🧵 Textiles","more":"✨ More"}
        await q.edit_message_text(f"{labels.get(cat,cat)}\n\nSelect a product:",reply_markup=cat_menu(cat))
    elif d.startswith("product:"):
        p=get_product(d.split(":")[1])
        if not p: return await q.edit_message_text("Product not found.",reply_markup=menu())
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Add to Cart",callback_data=f"add:{p['id']}")],
                                 [InlineKeyboardButton("📲 Order Now",callback_data=f"add:{p['id']}:now")],
                                 [InlineKeyboardButton("⬅️ Back",callback_data=f"cat:{p['category']}")]])
        text=f"*{p['name']}*\n\n{p['description']}\n\n💰 *{money(p['price'])}*"
        if p["photo_file_id"]:
            await q.message.reply_photo(p["photo_file_id"],caption=text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
            try: await q.message.delete()
            except: pass
        else: await q.edit_message_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
    elif d.startswith("add:"):
        pid=d.split(":")[1]; ct.user_data.setdefault("cart",{}); ct.user_data["cart"][pid]=ct.user_data["cart"].get(pid,0)+1
        if d.endswith(":now"): await checkout(u,ct)
        else: await q.edit_message_text("✅ Added to your cart.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 View Cart",callback_data="cart")],[InlineKeyboardButton("🛍️ Continue Shopping",callback_data="shop")]]))
    elif d=="cart": await show_cart(u,ct)
    elif d=="checkout": await checkout(u,ct)
    elif d=="clear": ct.user_data["cart"]={}; await q.edit_message_text("🛒 Your cart is empty.",reply_markup=menu())
    elif d=="orders": await orders_cmd(u,ct)
    elif d=="contact": await q.edit_message_text(f"📞 *{NAME}*\n\n{PHONE1}\n{PHONE2}\n\nWhatsApp: https://wa.me/{WHATSAPP}\nWebsite: {WEBSITE}",parse_mode=ParseMode.MARKDOWN,reply_markup=menu())
    elif d=="location": await q.edit_message_text(f"📍 *Our Location*\n\n{ADDRESS}",parse_mode=ParseMode.MARKDOWN,reply_markup=menu())

async def show_cart(u,ct):
    cart=ct.user_data.get("cart",{})
    if not cart:
        text="🛒 *Your cart is empty.*"; kb=menu()
    else:
        lines=["🛒 *Your Cart*\n"]; total=0; unknown=False
        for pid,qty in cart.items():
            p=get_product(pid)
            if not p: continue
            if p["price"] is None: unknown=True; sub="Price on request"
            else: sub=f"₦{p['price']*qty:,.0f}"; total+=p["price"]*qty
            lines.append(f"• {p['name']} × {qty} — {sub}")
        lines.append(f"\n*Total: {'Price on request' if unknown else f'₦{total:,.0f}'}*")
        text="\n".join(lines); kb=InlineKeyboardMarkup([[InlineKeyboardButton("📦 Checkout",callback_data="checkout")],[InlineKeyboardButton("🗑️ Clear Cart",callback_data="clear")],[InlineKeyboardButton("🛍️ Continue Shopping",callback_data="shop")]])
    if u.callback_query: await u.callback_query.edit_message_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
    else: await u.message.reply_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)

async def checkout(u,ct):
    cart=ct.user_data.get("cart",{})
    if not cart:
        return await (u.callback_query.edit_message_text("Your cart is empty.",reply_markup=menu()) if u.callback_query else u.message.reply_text("Your cart is empty.",reply_markup=menu()))
    total=0; unknown=False; items=[]
    for pid,qty in cart.items():
        p=get_product(pid)
        if p:
            items.append(f"{p['name']} × {qty}")
            if p["price"] is None: unknown=True
            else: total+=p["price"]*qty
    ct.user_data["items"]="\n".join(items); ct.user_data["total"]=None if unknown else total; ct.user_data["state"]="phone"
    await u.effective_message.reply_text("📦 *Checkout*\n\nPlease share your phone number.",parse_mode=ParseMode.MARKDOWN,reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 Share Phone Number",request_contact=True)]],resize_keyboard=True,one_time_keyboard=True))

async def text_handler(u,ct):
    state=ct.user_data.get("state")
    if state=="phone":
        ct.user_data["phone"]=u.message.contact.phone_number if u.message.contact else u.message.text
        ct.user_data["state"]="address"
        await u.message.reply_text("📍 Now send your delivery address.")
    elif state=="address":
        if u.message.text.lower()=="cancel": ct.user_data.clear(); return await u.message.reply_text("Checkout cancelled.",reply_markup=menu())
        await create_order(u,ct,u.message.text)
    elif admin(u):
        await u.message.reply_text("Admin: /admin_products /addproduct /setphoto /deleteproduct /admin_orders")

async def create_order(u,ct,address):
    uid=u.effective_user.id; username=u.effective_user.username or ""; name=u.effective_user.full_name
    total=ct.user_data.get("total"); items=ct.user_data.get("items"); phone=ct.user_data.get("phone","")
    c=conn(); cur=c.execute("INSERT INTO orders(user_id,username,customer_name,phone,address,items,total) VALUES(?,?,?,?,?,?,?)",(uid,username,name,phone,address,items,total or 0)); oid=cur.lastrowid; c.commit(); c.close()
    await ct.bot.send_message(ADMIN_ID,f"🔔 *NEW ORDER #{oid}*\n\nCustomer: {name}\nUsername: @{username or 'none'}\nTelegram ID: `{uid}`\nPhone: {phone}\nAddress: {address}\n\nItems:\n{items}\n\nTotal: {money(total)}",parse_mode=ParseMode.MARKDOWN)
    ct.user_data.clear()
    await u.message.reply_text(f"✅ *Order #{oid} received!*\n\nThank you for shopping with AD Fashion Hijabs & More.\nWe will contact you to confirm the order.",parse_mode=ParseMode.MARKDOWN,reply_markup=menu())

async def admin_products(u,ct):
    if not admin(u): return
    rows=get_products(); await u.message.reply_text("🛠️ Products\n\n"+"\n".join(f"{r['id']} | {r['name']} | {r['category']} | {money(r['price'])}" for r in rows) or "No products.")
async def addproduct(u,ct):
    if not admin(u): return
    parts=[x.strip() for x in u.message.text.partition(" ")[2].split("|")]
    if len(parts)<5: return await u.message.reply_text("Format: /addproduct ID | Name | Category | Price | Description\nUse 0 for price-on-request.")
    pid,name,cat,price,desc=parts[:5]; pv=None if price=="0" else int(price.replace(",","").replace("₦",""))
    c=conn(); c.execute("INSERT OR REPLACE INTO products(id,name,category,price,description,active) VALUES(?,?,?,?,?,1)",(pid,name,cat.lower(),pv,desc)); c.commit(); c.close()
    await u.message.reply_text(f"✅ Saved {name}")
async def deleteproduct(u,ct):
    if not admin(u): return
    pid=u.message.text.partition(" ")[2].strip(); c=conn(); c.execute("UPDATE products SET active=0 WHERE id=?",(pid,)); c.commit(); c.close(); await u.message.reply_text("✅ Product removed.")
async def setphoto(u,ct):
    if not admin(u): return
    pid=u.message.text.partition(" ")[2].strip()
    if not pid: return await u.message.reply_text("Use /setphoto PRODUCT_ID")
    ct.user_data["photo_target"]=pid; await u.message.reply_text("Now send the product photo.")
async def photo(u,ct):
    if not admin(u): return
    pid=ct.user_data.get("photo_target")
    if not pid: return await u.message.reply_text("Use /setphoto PRODUCT_ID first.")
    fid=u.message.photo[-1].file_id; c=conn(); c.execute("UPDATE products SET photo_file_id=? WHERE id=?",(fid,pid)); c.commit(); c.close(); ct.user_data.pop("photo_target",None); await u.message.reply_text("✅ Photo saved.")
async def admin_orders(u,ct):
    if not admin(u): return
    c=conn(); rows=c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 20").fetchall(); c.close()
    if not rows: return await u.message.reply_text("No orders yet.")
    for r in rows: await u.message.reply_text(f"📦 Order #{r['id']}\nCustomer: {r['customer_name']}\nPhone: {r['phone']}\nAddress: {r['address']}\nItems: {r['items']}\nTotal: {money(r['total'])}\nStatus: {r['status']}")

app_flask=Flask(__name__)
@app_flask.get("/")
def health(): return {"status":"ok","service":NAME}
def health_server(): app_flask.run(host="0.0.0.0",port=PORT,use_reloader=False)

async def main():
    init_db(); Thread(target=health_server,daemon=True).start()
    app=Application.builder().token(BOT_TOKEN).build()
    for cmd,fn in [("start",start),("help",help_cmd),("shop",shop),("cart",cart_cmd),("orders",orders_cmd),("contact",contact_cmd),("admin_products",admin_products),("addproduct",addproduct),("deleteproduct",deleteproduct),("setphoto",setphoto),("admin_orders",admin_orders)]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.PHOTO,photo))
    app.add_handler(MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND),text_handler))
    await app.initialize(); await app.start(); await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await asyncio.Event().wait()
if __name__=="__main__": asyncio.run(main())
