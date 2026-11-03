# AD FASHION HIJABS & MORE

Telegram shop bot + **AD FASHION SHOP** mobile web app (installable, free) for AD FASHION HIJABS & MORE, Bauchi, Nigeria.
Both channels run as **one service** and share **one database**, so products, prices, orders and news are the same everywhere.

## Files

| File | What it is |
|------|------------|
| `bot.py` | The Telegram bot. Also starts the web app. This is the file Railway runs. |
| `webshop.py` | The web app (Flask). Loaded by `bot.py`. Keep it next to `bot.py`. |
| `requirements.txt` | Python packages: python-telegram-bot, Flask, Pillow, pywebpush, cryptography. |

## Deploy on Railway

1. Put `bot.py`, `webshop.py` and `requirements.txt` in your GitHub repo (replace the old files).
2. In Railway, add a **Volume** and mount it at `/data`. Without it, data is lost on every redeploy.
3. Add the variables below, then redeploy. Wait for **Active**.
4. Under Settings, Networking, generate a domain. That address is your app link.

### Variables

| Variable | Required | Meaning |
|----------|----------|---------|
| `BOT_TOKEN` | yes | Bot token from BotFather. Keep it secret. If it ever leaks, send `/revoke` to BotFather and paste the new token. |
| `ADMIN_ID` | yes | Your Telegram numeric ID. Orders and alerts go here. |
| `DB_PATH` | yes | `/data/shop.db` (on the Volume). |
| `ADMIN_EMAIL` | yes | Email(s) that are admins in the app. Separate several with commas. |
| `SECRET_KEY` | recommended | Any long random text. Keeps app logins stable. |
| `GOOGLE_CLIENT_ID` | optional | Turns on "Continue with Google" (see below). |
| `PAYSTACK_SECRET`, `SITE_URL` | optional | Turns on online card payment. `SITE_URL` is your app address. |

## First-time setup (do once)

1. Open the bot, send `/start`, then **Admin, Settings**.
2. **🏷️ App Logo**: send your logo as a photo (square works best). It becomes the app icon, opening screen and top-bar logo.
3. **📣 App Flyer**: send the AD FASHION HIJABS AND MORE flyer. It shows on the Create account / Log in page.
4. Open the app address, create an account with your `ADMIN_EMAIL`, tap the admin icon and **create your 6-digit admin PIN**.
5. Add products. For Hijabs and Jilbabs, set the three fabric prices (Dan Kaduna, Zoom, Egyptian).

## What the app does

**Customers**
- Must register first (name, email, phone number, password) or use Google. They stay logged in until they sign out. They can log in with email or phone.
- Home, Shop, News, My Orders, Profile. Notification bell in the top bar.
- Swipeable product galleries, colour swatches, fabric price cards (Hijabs and Jilbabs only), wishlist, cart, checkout.
- **New arrivals**: products added in the last 30 days (tab on Home, "See all" shows only those). The Shop shows everything A to Z.
- Order tracking with a progress bar; after delivery they press **RECEIVED AND THANK YOU**.
- Style points: 10 for joining, then 1 point per ₦1,000 spent, added when the order is delivered.
- News with pictures, likes, comments and replies. Phone notifications for order updates and news.

**Admin (inside the app, same data as Telegram)**
- Dashboard, orders and status updates, products (photos, galleries, fabric prices), categories and types, colours, news, settings (logo, flyer), customers (full profile for each), **💛 Thank you** page for WhatsApp thank-you messages, 🔐 PIN.
- **Admin PIN**: asked every time the app is opened, after logging in, and after 10 minutes of no activity. 5 wrong tries block it for 10 minutes. Forgot it? In Telegram: Admin, Settings, **🔐 Reset App PIN**.

**Telegram bot**
- Categories and subcategories, galleries, cart with live quantity buttons, discount, delivery fees, welcome message, orders, style requests. Admin menus manage all of it, including fabric prices and categories.

## Notifications

- **You (admin)** get a phone notification and a Telegram message for new sign-ups, new orders, "RECEIVED AND THANK YOU" and Paystack payments.
- **Customers** get a phone notification whenever their order status changes, and for news. Everything also appears under the bell.
- Each person turns phone notifications on once with the "Turn on" bar. On **iPhone** this only works after Add to Home Screen (iOS 16.4 or newer).

## Install on a phone

- **Android (Chrome):** open the link, log in, three dots, **Install app**.
- **iPhone (Safari only):** open the link, Share button, **Add to Home Screen**.
- If you change the logo later, uninstall the app and install it again so the phone shows the new icon.

## Google sign-in (optional, free)

1. console.cloud.google.com: create a project, set up the OAuth consent screen (app name AD FASHION SHOP).
2. Credentials, Create credentials, OAuth client ID, **Web application**.
3. Add your Railway address as an **Authorised JavaScript origin**.
4. Copy the Client ID (ends in `.apps.googleusercontent.com`) into the `GOOGLE_CLIENT_ID` variable and redeploy.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Data disappeared after a deploy | The Volume is missing or not mounted at `/data`, or `DB_PATH` is wrong. |
| App icon is a gold ring | Logo not saved. Open `YOUR-ADDRESS/icon-512.png`: if it shows the ring, send the logo again in the bot; if it shows your logo, uninstall and reinstall the app. |
| No admin icon | The email you registered with must match `ADMIN_EMAIL`. |
| No phone notifications | Check `pywebpush` and `cryptography` are in `requirements.txt`; tap **Turn on** in the app; on iPhone install to Home Screen first. |
| Google button missing | `GOOGLE_CLIENT_ID` not set, or not redeployed. |
| Customers or admin can't see new pictures | Pictures live in Telegram. The bot must stay running and `BOT_TOKEN` must be valid. |

## Known limits

- Product videos can be uploaded in Telegram, not from the web admin.
- Customers who ordered through Telegram get Telegram messages; app customers get app notifications.
- WhatsApp thank-you messages are one tap per customer (fully automatic sending needs the paid WhatsApp Business service).

Built for Comr. Muhammad AD Muhammad, Bauchi, Nigeria.
