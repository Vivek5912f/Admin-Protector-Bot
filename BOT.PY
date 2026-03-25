import os
import sqlite3
import logging
import warnings
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ─── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("8668220798:AAHwlduJGKO1XWpXM_3uX0k_vKXolX7ieoo", "")
ADMIN_ID  = 7964023243        # ← Apna Telegram ID
DB_PATH   = "bot_data.db"

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logging.getLogger("telegram.ext.conversationhandler").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning)
logger = logging.getLogger("KeyBot")

# ─── Conversation States ───────────────────────────────────────────────────────

(
    VERIFY_AMOUNT,
    VERIFY_UTR,
    ADMIN_ADD_PRODUCT_NAME,
    ADMIN_ADD_PRODUCT_PRICE1,
    ADMIN_ADD_PRODUCT_PRICE7,
    ADMIN_ADD_PRODUCT_PRICE30,
    ADMIN_ADD_KEYS_PICK_PRODUCT,
    ADMIN_ADD_KEYS_PICK_PLAN,
    ADMIN_ADD_KEYS_INPUT,
    ADMIN_ADD_BALANCE_UID,
    ADMIN_ADD_BALANCE_AMT,
    ADMIN_SET_UPI_ID,
    ADMIN_SET_UPI_NAME,
) = range(13)

# ─── Database Setup ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                balance     REAL    DEFAULT 0,
                referrer_id INTEGER DEFAULT NULL,
                joined_at   TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                price_1     REAL    NOT NULL,
                price_7     REAL    NOT NULL,
                price_30    REAL    NOT NULL,
                active      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS keys (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL,
                plan        TEXT    NOT NULL,
                key_value   TEXT    NOT NULL,
                used        INTEGER DEFAULT 0,
                FOREIGN KEY (product_id) REFERENCES products(id)
            );

            CREATE TABLE IF NOT EXISTS payments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                amount      REAL    NOT NULL,
                utr         TEXT    NOT NULL,
                status      TEXT    DEFAULT 'pending',
                created_at  TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                product_id  INTEGER NOT NULL,
                plan        TEXT    NOT NULL,
                key_value   TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                purchased_at TEXT   DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL
            );
        """)
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('upi_id', 'yourname@upi')")
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('upi_name', 'Key Store')")
        db.commit()
    logger.info("Database initialised.")


# ─── Settings Helpers ──────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        db.commit()


def get_upi_info() -> tuple[str, str]:
    return get_setting("upi_id", "yourname@upi"), get_setting("upi_name", "Key Store")


# ─── User Helpers ──────────────────────────────────────────────────────────────

def ensure_user(user_id: int, username: str, full_name: str, referrer_id: int | None = None) -> bool:
    with get_db() as db:
        existing = db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO users (user_id, username, full_name, referrer_id) VALUES (?,?,?,?)",
                (user_id, username, full_name, referrer_id),
            )
            if referrer_id and referrer_id != user_id:
                db.execute("UPDATE users SET balance = balance + 10 WHERE user_id=?", (referrer_id,))
            db.commit()
            return True
        return False


def get_balance(user_id: int) -> float:
    with get_db() as db:
        row = db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        return float(row["balance"]) if row else 0.0


def add_balance(user_id: int, amount: float):
    with get_db() as db:
        db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        db.commit()


def deduct_balance(user_id: int, amount: float):
    with get_db() as db:
        db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
        db.commit()


def total_users() -> int:
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        return row["cnt"] if row else 0


# ─── Product Helpers ───────────────────────────────────────────────────────────

def get_all_products():
    with get_db() as db:
        return db.execute("SELECT * FROM products WHERE active=1 ORDER BY id").fetchall()


def get_product(product_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()


# ─── Key Helpers ───────────────────────────────────────────────────────────────

def get_available_key(product_id: int, plan: str):
    with get_db() as db:
        return db.execute(
            "SELECT id, key_value FROM keys WHERE product_id=? AND plan=? AND used=0 LIMIT 1",
            (product_id, plan),
        ).fetchone()


def mark_key_used(key_id: int):
    with get_db() as db:
        db.execute("UPDATE keys SET used=1 WHERE id=?", (key_id,))
        db.commit()


def count_keys(product_id: int, plan: str) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) as cnt FROM keys WHERE product_id=? AND plan=? AND used=0",
            (product_id, plan),
        ).fetchone()
        return row["cnt"] if row else 0


# ─── Payment Helpers ───────────────────────────────────────────────────────────

def save_payment(user_id: int, amount: float, utr: str) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO payments (user_id, amount, utr) VALUES (?,?,?)",
            (user_id, amount, utr),
        )
        db.commit()
        return cur.lastrowid


def get_payment(payment_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()


def update_payment_status(payment_id: int, status: str):
    with get_db() as db:
        db.execute("UPDATE payments SET status=? WHERE id=?", (status, payment_id))
        db.commit()


def get_pending_payments():
    with get_db() as db:
        return db.execute(
            "SELECT p.*, u.username, u.full_name FROM payments p "
            "JOIN users u ON u.user_id=p.user_id "
            "WHERE p.status='pending' ORDER BY p.created_at DESC"
        ).fetchall()


def save_purchase(user_id, product_id, plan, key_value, amount):
    with get_db() as db:
        db.execute(
            "INSERT INTO purchases (user_id,product_id,plan,key_value,amount) VALUES (?,?,?,?,?)",
            (user_id, product_id, plan, key_value, amount),
        )
        db.commit()


# ─── UI Helpers ────────────────────────────────────────────────────────────────

PLAN_LABELS = {"1": "1 Day", "7": "7 Days", "30": "30 Days"}


def plan_price(product, plan: str) -> float:
    return {"1": float(product["price_1"]), "7": float(product["price_7"]), "30": float(product["price_30"])}[plan]


def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("💰 Balance",      callback_data="menu_balance"),
            InlineKeyboardButton("➕ Add Fund",     callback_data="menu_addfund"),
        ],
        [
            InlineKeyboardButton("🛒 Shop",         callback_data="menu_shop"),
            InlineKeyboardButton("🔗 Refer & Earn", callback_data="menu_refer"),
        ],
    ]
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton("🔧 Admin Panel", callback_data="menu_admin")])
    return InlineKeyboardMarkup(buttons)


def back_button(callback: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=callback)]])


def admin_panel_keyboard(pending: int, users: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Product",                  callback_data="admin_add_product")],
        [InlineKeyboardButton("🔑 Add Keys",                     callback_data="admin_add_keys")],
        [InlineKeyboardButton("💰 Add Balance to User",          callback_data="admin_add_balance")],
        [InlineKeyboardButton("⚙️ Set UPI Details",             callback_data="admin_set_upi")],
        [InlineKeyboardButton(f"👥 Total Users: {users}",       callback_data="admin_noop")],
        [InlineKeyboardButton(f"⏳ Pending Payments: {pending}", callback_data="admin_view_pending")],
        [InlineKeyboardButton("⬅️ Back",                        callback_data="menu_main")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    args     = context.args
    referrer = int(args[0]) if args and args[0].isdigit() else None
    is_new   = ensure_user(user.id, user.username or "", user.full_name, referrer)

    if is_new and referrer:
        try:
            await context.bot.send_message(
                referrer,
                "🎉 *Someone joined using your referral link!*\n₹10 has been added to your wallet.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    await update.message.reply_text(
        f"👋 *Welcome to Key Store, {user.first_name}!*\n\n"
        f"🔑 Buy product keys instantly with wallet balance.\n"
        f"💳 Add funds via UPI — approved in minutes.\n\n"
        f"Use the menu below:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(user.id),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main Callback Router
# ══════════════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data
    user  = query.from_user
    ensure_user(user.id, user.username or "", user.full_name)

    if data == "menu_main":
        await query.edit_message_text(
            f"👋 *Main Menu*\n\nHello {user.first_name}! Choose an option:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(user.id),
        )

    elif data == "menu_balance":
        bal = get_balance(user.id)
        await query.edit_message_text(
            f"💰 *Your Wallet Balance*\n\n"
            f"┌──────────────────────────┐\n"
            f"│   💵  Balance: ₹{bal:.2f}\n"
            f"└──────────────────────────┘\n\n"
            f"Tap *Add Fund* to top up.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button(),
        )

    elif data == "menu_shop":
        products = get_all_products()
        if not products:
            await query.edit_message_text(
                "🛒 *Shop*\n\nNo products available right now. Check back soon!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button(),
            )
            return
        buttons = [
            [InlineKeyboardButton(f"📦 {p['name']}  [ID: {p['id']}]", callback_data=f"product_{p['id']}")]
            for p in products
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
        await query.edit_message_text(
            "🛒 *Shop — Choose a Product*\n\nSelect a product to view plans:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("product_"):
        pid     = int(data.split("_")[1])
        product = get_product(pid)
        if not product:
            await query.edit_message_text("❌ Product not found.", reply_markup=back_button("menu_shop"))
            return

        def stock(plan):
            n = count_keys(pid, plan)
            return f"✅ ({n})" if n > 0 else "❌ Out of stock"

        buttons = [
            [InlineKeyboardButton(f"🕐 1 Day — ₹{product['price_1']:.2f}  {stock('1')}",    callback_data=f"buy_{pid}_1")],
            [InlineKeyboardButton(f"📅 7 Days — ₹{product['price_7']:.2f}  {stock('7')}",   callback_data=f"buy_{pid}_7")],
            [InlineKeyboardButton(f"📆 30 Days — ₹{product['price_30']:.2f}  {stock('30')}", callback_data=f"buy_{pid}_30")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu_shop")],
        ]
        await query.edit_message_text(
            f"📦 *{product['name']}*\n\n"
            f"Choose your plan:\n"
            f"✅ (n) = In stock   ❌ = Out of stock",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("buy_"):
        parts     = data.split("_")
        pid, plan = int(parts[1]), parts[2]
        product   = get_product(pid)
        if not product:
            await query.edit_message_text("❌ Product not found.", reply_markup=back_button("menu_shop"))
            return

        price   = plan_price(product, plan)
        balance = get_balance(user.id)

        if balance < price:
            await query.edit_message_text(
                f"❌ *Insufficient Balance*\n\n"
                f"💰 Your Balance: ₹{balance:.2f}\n"
                f"🏷️ Required:    ₹{price:.2f}\n"
                f"🔻 Shortfall:   ₹{price - balance:.2f}\n\n"
                f"Please top up your wallet first.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Fund", callback_data="menu_addfund")],
                    [InlineKeyboardButton("⬅️ Back",     callback_data=f"product_{pid}")],
                ]),
            )
            return

        key_row = get_available_key(pid, plan)
        if not key_row:
            await query.edit_message_text(
                f"😔 *Out of Stock*\n\n"
                f"No keys available for *{product['name']}* ({PLAN_LABELS[plan]}) right now.\n"
                f"Please check back later.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button(f"product_{pid}"),
            )
            return

        deduct_balance(user.id, price)
        mark_key_used(key_row["id"])
        save_purchase(user.id, pid, plan, key_row["key_value"], price)
        logger.info(f"Purchase: user={user.id} product={product['name']} plan={plan} price={price}")

        await query.edit_message_text(
            f"✅ *Purchase Successful!*\n\n"
            f"📦 Product : *{product['name']}*\n"
            f"📅 Plan    : *{PLAN_LABELS[plan]}*\n"
            f"💳 Paid    : *₹{price:.2f}*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 *Your Key:*\n\n"
            f"`{key_row['key_value']}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Tap the key to copy it. Enjoy! 🎉",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Buy More",  callback_data="menu_shop")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
            ]),
        )
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"🛒 *New Purchase!*\n\n"
                f"👤 User   : [{user.full_name}](tg://user?id={user.id}) (`{user.id}`)\n"
                f"📦 Product: *{product['name']}*\n"
                f"📅 Plan   : *{PLAN_LABELS[plan]}*\n"
                f"💰 Amount : ₹{price:.2f}\n"
                f"🔑 Key    : `{key_row['key_value']}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    elif data == "menu_refer":
        me   = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={user.id}"
        await query.edit_message_text(
            f"🔗 *Refer & Earn*\n\n"
            f"Share your unique link and earn *₹10* for every new user!\n\n"
            f"👇 *Your Referral Link:*\n`{link}`\n\n"
            f"Tap the link above to copy it.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button(),
        )

    elif data.startswith("approve_") or data.startswith("reject_"):
        if user.id != ADMIN_ID:
            await query.answer("⛔ Unauthorised", show_alert=True)
            return
        action, pid_str = data.split("_", 1)
        payment_id = int(pid_str)
        payment    = get_payment(payment_id)

        if not payment:
            await query.edit_message_text("❌ Payment not found.")
            return
        if payment["status"] != "pending":
            await query.edit_message_text(
                f"ℹ️ Payment #{payment_id} is already *{payment['status']}*.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if action == "approve":
            add_balance(payment["user_id"], payment["amount"])
            update_payment_status(payment_id, "approved")
            await query.edit_message_text(
                f"✅ *Payment #{payment_id} Approved!*\n"
                f"₹{payment['amount']:.2f} added to user `{payment['user_id']}`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            try:
                await context.bot.send_message(
                    payment["user_id"],
                    f"✅ *Payment Approved!*\n\n"
                    f"₹*{payment['amount']:.2f}* has been credited to your wallet.\n"
                    f"UTR: `{payment['utr']}`\n\n"
                    f"Happy shopping! 🛒",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        else:
            update_payment_status(payment_id, "rejected")
            await query.edit_message_text(
                f"❌ *Payment #{payment_id} Rejected.*",
                parse_mode=ParseMode.MARKDOWN,
            )
            try:
                await context.bot.send_message(
                    payment["user_id"],
                    f"❌ *Payment Rejected*\n\n"
                    f"Your request of ₹{payment['amount']:.2f} (UTR: `{payment['utr']}`) "
                    f"was rejected.\n\nIf you believe this is a mistake, contact support.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

    elif data == "menu_admin":
        if user.id != ADMIN_ID:
            await query.answer("⛔ Unauthorised", show_alert=True)
            return
        upi_id, upi_name = get_upi_info()
        pending = len(get_pending_payments())
        users   = total_users()
        await query.edit_message_text(
            f"🔧 *Admin Panel*\n\n"
            f"🏦 UPI ID  : `{upi_id}`\n"
            f"👤 UPI Name: *{upi_name}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_panel_keyboard(pending, users),
        )

    elif data == "admin_noop":
        await query.answer()

    elif data == "admin_view_pending":
        if user.id != ADMIN_ID:
            await query.answer("⛔ Unauthorised", show_alert=True)
            return
        payments = get_pending_payments()
        if not payments:
            await query.edit_message_text(
                "⏳ *Pending Payments*\n\nNo pending payments right now. ✅",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("menu_admin"),
            )
            return
        text = "⏳ *Pending Payments*\n\n"
        for p in payments:
            name = p["full_name"] or p["username"] or str(p["user_id"])
            text += (
                f"*#{p['id']}* — {name} (`{p['user_id']}`)\n"
                f"  💰 Amount: ₹{p['amount']:.2f}\n"
                f"  📋 UTR: `{p['utr']}`\n"
                f"  🕐 Time: {p['created_at']}\n\n"
            )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("menu_admin"),
        )

    elif data in ("admin_add_product", "admin_add_keys", "admin_add_balance",
                  "admin_set_upi", "menu_addfund"):
        await query.answer("⚠️ Session expired. Send /start and try again.", show_alert=True)


# ══════════════════════════════════════════════════════════════════════════════
# Add Fund — UPI page
# ══════════════════════════════════════════════════════════════════════════════

async def addfund_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ensure_user(query.from_user.id, query.from_user.username or "", query.from_user.full_name)
    upi_id, upi_name = get_upi_info()
    await query.edit_message_text(
        f"➕ *Add Fund via UPI*\n\n"
        f"1️⃣  Payment bhejo neeche wale UPI pe\n"
        f"2️⃣  *Verify Payment* tap karo & Amount + UTR daalo\n"
        f"3️⃣  Admin approve karega ⚡\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 *UPI ID :* `{upi_id}`\n"
        f"👤 *Name   :* {upi_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️  Minimum: ₹10",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Verify Payment", callback_data="verify_start")],
            [InlineKeyboardButton("⬅️ Back",           callback_data="menu_main")],
        ]),
    )
    return ConversationHandler.END


# ── Verify Payment ─────────────────────────────────────────────────────────────

async def verify_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💬 *Verify Payment — Step 1 of 2*\n\n"
        "Amount kitna diya? (sirf number):\n\n"
        "Example: `250`\n\n"
        "Cancel karne ke liye /cancel bhejo.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return VERIFY_AMOUNT


async def verify_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Sirf number daalo (e.g. `250`):", parse_mode=ParseMode.MARKDOWN)
        return VERIFY_AMOUNT
    context.user_data["verify_amount"] = amount
    await update.message.reply_text(
        f"✅ Amount: ₹{amount:.2f}\n\n"
        f"💬 *Step 2 of 2* — Ab apna *UTR / Transaction ID* daalo:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return VERIFY_UTR


async def verify_utr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    utr    = update.message.text.strip()
    amount = context.user_data.get("verify_amount", 0)
    user   = update.effective_user

    if len(utr) < 6:
        await update.message.reply_text("❌ UTR bahut chhota lag raha hai. Dobara daalo:")
        return VERIFY_UTR

    payment_id = save_payment(user.id, amount, utr)
    await update.message.reply_text(
        f"📤 *Request Submit Ho Gayi!*\n\n"
        f"💰 Amount : ₹{amount:.2f}\n"
        f"📋 UTR    : `{utr}`\n\n"
        f"Admin approve karega toh balance credit ho jaayega. ⏳",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(user.id),
    )
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"💳 *New Fund Request #{payment_id}*\n\n"
            f"👤 User  : [{user.full_name}](tg://user?id={user.id}) (`{user.id}`)\n"
            f"💰 Amount: ₹{amount:.2f}\n"
            f"📋 UTR   : `{utr}`\n"
            f"🕐 Time  : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{payment_id}"),
                    InlineKeyboardButton("❌ Reject",  callback_data=f"reject_{payment_id}"),
                ]
            ]),
        )
    except Exception as e:
        logger.error(f"Admin notify failed: {e}")
    context.user_data.clear()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Add Product
# ══════════════════════════════════════════════════════════════════════════════

async def admin_add_product_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Unauthorised", show_alert=True)
        return ConversationHandler.END
    await query.edit_message_text(
        "📦 *Add New Product — Step 1 of 4*\n\nProduct ka *naam* daalo:\n\n/cancel se band karo.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_PRODUCT_NAME


async def admin_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["np_name"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Name: *{context.user_data['np_name']}*\n\nStep 2 of 4 — *1-day price* (₹):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_PRODUCT_PRICE1


async def admin_product_price1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["np_price1"] = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid. Number daalo (e.g. `49`):", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_ADD_PRODUCT_PRICE1
    await update.message.reply_text(
        f"✅ 1-day: ₹{context.user_data['np_price1']:.2f}\n\nStep 3 of 4 — *7-day price* (₹):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_PRODUCT_PRICE7


async def admin_product_price7(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["np_price7"] = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid. Number daalo:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_ADD_PRODUCT_PRICE7
    await update.message.reply_text(
        f"✅ 7-day: ₹{context.user_data['np_price7']:.2f}\n\nStep 4 of 4 — *30-day price* (₹):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_PRODUCT_PRICE30


async def admin_product_price30(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price30 = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid. Number daalo:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_ADD_PRODUCT_PRICE30

    name, p1, p7 = context.user_data["np_name"], context.user_data["np_price1"], context.user_data["np_price7"]
    with get_db() as db:
        db.execute(
            "INSERT INTO products (name, price_1, price_7, price_30) VALUES (?,?,?,?)",
            (name, p1, p7, price30),
        )
        db.commit()
    await update.message.reply_text(
        f"✅ *Product Add Ho Gaya!*\n\n"
        f"📦 Name   : *{name}*\n"
        f"🕐 1-day  : ₹{p1:.2f}\n"
        f"📅 7-day  : ₹{p7:.2f}\n"
        f"📆 30-day : ₹{price30:.2f}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(update.effective_user.id),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Add Keys
# ══════════════════════════════════════════════════════════════════════════════

async def admin_add_keys_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Unauthorised", show_alert=True)
        return ConversationHandler.END
    products = get_all_products()
    if not products:
        await query.edit_message_text("❌ Pehle ek product add karo.", reply_markup=back_button("menu_admin"))
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(f"{p['name']} [ID:{p['id']}]", callback_data=f"akprod_{p['id']}")]
        for p in products
    ]
    buttons.append([InlineKeyboardButton("⬅️ Cancel", callback_data="menu_admin")])
    await query.edit_message_text(
        "🔑 *Add Keys — Step 1: Product Select Karo*\n\n/cancel se band karo.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ADMIN_ADD_KEYS_PICK_PRODUCT


async def admin_add_keys_pick_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[1])
    context.user_data["ak_pid"] = pid
    product = get_product(pid)
    buttons = [
        [InlineKeyboardButton("🕐 1 Day",   callback_data="akplan_1")],
        [InlineKeyboardButton("📅 7 Days",  callback_data="akplan_7")],
        [InlineKeyboardButton("📆 30 Days", callback_data="akplan_30")],
        [InlineKeyboardButton("⬅️ Cancel",  callback_data="menu_admin")],
    ]
    await query.edit_message_text(
        f"🔑 *{product['name']}* ke liye plan chuno:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ADMIN_ADD_KEYS_PICK_PLAN


async def admin_add_keys_pick_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    plan = query.data.split("_")[1]
    context.user_data["ak_plan"] = plan
    await query.edit_message_text(
        f"🔑 *Keys Add Karo — {PLAN_LABELS[plan]}*\n\n"
        f"Ek line mein ek key bhejo:\n\n"
        f"```\nKEY-ABCD-1234\nKEY-EFGH-5678\n```\n\n"
        f"/cancel se band karo.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_KEYS_INPUT


async def admin_add_keys_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_keys = [k.strip() for k in update.message.text.strip().splitlines() if k.strip()]
    if not raw_keys:
        await update.message.reply_text("❌ Koi key nahi mili. Ek line mein ek key bhejo:")
        return ADMIN_ADD_KEYS_INPUT
    pid  = context.user_data["ak_pid"]
    plan = context.user_data["ak_plan"]
    with get_db() as db:
        db.executemany(
            "INSERT INTO keys (product_id, plan, key_value) VALUES (?,?,?)",
            [(pid, plan, k) for k in raw_keys],
        )
        db.commit()
    product = get_product(pid)
    await update.message.reply_text(
        f"✅ *{len(raw_keys)} key(s) add ho gayi!*\n\n"
        f"📦 Product: *{product['name']}*\n"
        f"📅 Plan   : *{PLAN_LABELS[plan]}*\n"
        f"🔢 Stock  : {count_keys(pid, plan)} keys available",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(update.effective_user.id),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Add Balance
# ══════════════════════════════════════════════════════════════════════════════

async def admin_add_balance_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Unauthorised", show_alert=True)
        return ConversationHandler.END
    await query.edit_message_text(
        "💰 *Add Balance — Step 1 of 2*\n\nUser ID daalo jisko credit karna hai:\n\n/cancel se band karo.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_BALANCE_UID


async def admin_balance_uid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Invalid User ID. Sirf numbers:")
        return ADMIN_ADD_BALANCE_UID
    context.user_data["bal_uid"] = int(text)
    await update.message.reply_text(
        f"👤 User ID: `{text}`\n\nStep 2 of 2 — Kitna *amount* add karna hai (₹):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_BALANCE_AMT


async def admin_balance_amt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid. Positive number daalo:")
        return ADMIN_ADD_BALANCE_AMT
    uid = context.user_data["bal_uid"]
    add_balance(uid, amount)
    new_bal = get_balance(uid)
    await update.message.reply_text(
        f"✅ *Balance Add Ho Gaya!*\n\n"
        f"👤 User ID    : `{uid}`\n"
        f"➕ Added      : ₹{amount:.2f}\n"
        f"💰 New Balance: ₹{new_bal:.2f}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(update.effective_user.id),
    )
    try:
        await context.bot.send_message(
            uid,
            f"💰 *Balance Credit Hua!*\n\n"
            f"Admin ne ₹*{amount:.2f}* add kiya.\n"
            f"Current balance: ₹{new_bal:.2f}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass
    context.user_data.clear()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Set UPI Details
# ══════════════════════════════════════════════════════════════════════════════

async def admin_set_upi_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Unauthorised", show_alert=True)
        return ConversationHandler.END
    upi_id, upi_name = get_upi_info()
    await query.edit_message_text(
        f"⚙️ *UPI Details Set Karo — Step 1 of 2*\n\n"
        f"Current UPI ID: `{upi_id}`\n\n"
        f"Naya *UPI ID* daalo:\n"
        f"Example: `myname@paytm` ya `9876543210@upi`\n\n"
        f"/cancel se band karo.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_SET_UPI_ID


async def admin_set_upi_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    upi_id = update.message.text.strip()
    if len(upi_id) < 3 or "@" not in upi_id:
        await update.message.reply_text(
            "❌ Invalid UPI ID. '@' hona chahiye (e.g. `name@upi`):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ADMIN_SET_UPI_ID
    context.user_data["new_upi_id"] = upi_id
    _, upi_name = get_upi_info()
    await update.message.reply_text(
        f"✅ UPI ID: `{upi_id}`\n\n"
        f"Step 2 of 2 — *Display Name* daalo (jo users ko dikhega):\n"
        f"Current: *{upi_name}*\n\n"
        f"Example: `Ravi Key Shop`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_SET_UPI_NAME


async def admin_set_upi_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    upi_name = update.message.text.strip()
    if len(upi_name) < 2:
        await update.message.reply_text("❌ Name bahut chhota hai. Dobara daalo:")
        return ADMIN_SET_UPI_NAME
    upi_id = context.user_data["new_upi_id"]
    set_setting("upi_id",   upi_id)
    set_setting("upi_name", upi_name)
    logger.info(f"UPI updated — ID: {upi_id}, Name: {upi_name}")
    await update.message.reply_text(
        f"✅ *UPI Details Update Ho Gaye!*\n\n"
        f"🏦 UPI ID  : `{upi_id}`\n"
        f"👤 UPI Name: *{upi_name}*\n\n"
        f"Ab users ko Add Fund mein yahi dikhega.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(update.effective_user.id),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# Cancel & Unknown
# ══════════════════════════════════════════════════════════════════════════════

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=main_menu_keyboard(update.effective_user.id),
        )
    return ConversationHandler.END


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or "", user.full_name)
    await update.message.reply_text(
        "👇 Menu buttons use karo:",
        reply_markup=main_menu_keyboard(user.id),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def build_conv(entry_pattern, entry_fn, states, extra_entries=None) -> ConversationHandler:
    entries = [CallbackQueryHandler(entry_fn, pattern=f"^{entry_pattern}$")]
    if extra_entries:
        entries += extra_entries
    return ConversationHandler(
        entry_points=entries,
        states=states,
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_chat=True,
        per_message=False,
    )


def main():
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN set nahi hai!")
        return

    init_db()
    logger.info("Key Store Bot start ho raha hai...")

    app = Application.builder().token(BOT_TOKEN).build()

    addfund_conv     = build_conv("menu_addfund",       addfund_entry,            states={})
    verify_conv      = build_conv("verify_start",       verify_start,             states={
        VERIFY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_amount)],
        VERIFY_UTR:    [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_utr)],
    })
    add_product_conv = build_conv("admin_add_product",  admin_add_product_entry,  states={
        ADMIN_ADD_PRODUCT_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_product_name)], **...**

_This response is too long to display in full._