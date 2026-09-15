# AD Fashion Hijabs & More — Telegram Bot

## Features
- Main menu and product categories
- Starter catalogue
- Shopping cart and checkout
- Customer phone/address collection
- Order storage and admin notifications
- Admin product commands and product photo upload
- Website, WhatsApp, contact and location buttons

## Security
Never commit the BotFather token. Set `BOT_TOKEN` privately in Render.
Admin Telegram ID: 6921853187

## Render
Build: `pip install -r requirements.txt`
Start: `python bot.py`

Environment:
- `BOT_TOKEN` = your NEW private BotFather token
- `ADMIN_ID` = `6921853187`
- `PYTHON_VERSION` = `3.13.5`

## Admin commands
`/admin_products`
`/addproduct ID | Name | Category | Price | Description`
`/setphoto PRODUCT_ID` then send a photo
`/deleteproduct PRODUCT_ID`
`/admin_orders`

Use price `0` for price-on-request.

## Note
The starter database is SQLite. It is suitable for testing/starter use. For a production store, move orders/products to persistent PostgreSQL storage.
