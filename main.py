# main.py
# Telegram ordering bot - Late Nite Lube
# Requirements: python-telegram-bot==20.5

import os
import logging
import smtplib
import asyncio
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters, ContextTypes
)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prevent token exposure in logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ---------- Config (from Replit secrets) ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
LATE_NITE_EMAIL = os.getenv("LATE_NITE_EMAIL", "orders@latenitelube.com")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set (Replit Secrets).")
if not SMTP_USER or not SMTP_PASSWORD:
    logger.warning("SMTP_USER or SMTP_PASSWORD not set. Email sending will fail until configured.")

# ---------- Product catalog ----------
CATALOG = [
    {"id": "p1", "name": "2oz Silicone-based personal lubricant", "price": 19.99},
    {"id": "p2", "name": "2oz Silicone-based personal lubricant (2-pack)", "price": 29.99},
    {"id": "p3", "name": "4oz Silicone-based personal lubricant", "price": 29.99},
    {"id": "p4", "name": "4oz Silicone-based personal lubricant (2-pack)", "price": 39.99},
]

# In-memory carts and conversation states
CARTS = {}  # user_id -> list of items
ADDRESS, PHONE = range(2)

# ---------- Helpers ----------
def format_price(v: float) -> str:
    return f"${v:.2f}"

def get_product_by_id(pid: str):
    return next((p for p in CATALOG if p["id"] == pid), None)

def cart_summary(user_id: int) -> str:
    cart = CARTS.get(user_id, [])
    if not cart:
        return "Your cart is empty."
    lines = []
    total = 0.0
    for it in cart:
        lines.append(f'{it["qty"]}× {it["name"]} — {format_price(it["price"])} each')
        total += it["qty"] * it["price"]
    lines.append(f"\nTotal: {format_price(total)}")
    return "\n".join(lines)

# ---------- Telegram command handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("View Menu", callback_data="show_menu")],
            [InlineKeyboardButton("View Cart", callback_data="view_cart")]
        ])
        await update.message.reply_text("Welcome to Late Nite Lube! Use the buttons below to start:", reply_markup=keyboard)

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Can be called from /menu or inline buttons
    keyboard = [
        [
            InlineKeyboardButton(f'{CATALOG[0]["name"]} ({format_price(CATALOG[0]["price"])})', callback_data="add:p1"),
            InlineKeyboardButton(f'{CATALOG[1]["name"]} ({format_price(CATALOG[1]["price"])})', callback_data="add:p2")
        ],
        [
            InlineKeyboardButton(f'{CATALOG[2]["name"]} ({format_price(CATALOG[2]["price"])})', callback_data="add:p3"),
            InlineKeyboardButton(f'{CATALOG[3]["name"]} ({format_price(CATALOG[3]["price"])})', callback_data="add:p4")
        ],
        [InlineKeyboardButton("View Cart", callback_data="view_cart")]
    ]
    if update.message:
        await update.message.reply_text("Select a product to add to your cart:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        await update.callback_query.edit_message_text("Select a product to add to your cart:", reply_markup=InlineKeyboardMarkup(keyboard))

async def cart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        uid = update.effective_user.id
        summary = cart_summary(uid)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Checkout", callback_data="start_checkout")],
            [InlineKeyboardButton("Clear Cart", callback_data="clear_cart")],
            [InlineKeyboardButton("Back to Menu", callback_data="show_menu")]
        ])
        if update.message:
            await update.message.reply_text(summary, reply_markup=keyboard)
        elif update.callback_query:
            await update.callback_query.edit_message_text(summary, reply_markup=keyboard)

async def clearcart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and update.callback_query:
        CARTS.pop(update.effective_user.id, None)
        await update.callback_query.edit_message_text("Cart cleared.")
        await show_menu(update, context)

# ---------- Button handler ----------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.from_user:
        return
    await q.answer()
    data = q.data or ""
    uid = q.from_user.id

    if data.startswith("add:"):
        pid = data.split(":", 1)[1]
        p = get_product_by_id(pid)
        if not p:
            await q.edit_message_text("Product not found.")
            return
        cart = CARTS.setdefault(uid, [])
        for it in cart:
            if it["prod_id"] == pid:
                it["qty"] += 1
                break
        else:
            cart.append({"prod_id": pid, "name": p["name"], "price": p["price"], "qty": 1})
        await q.edit_message_text(f'Added 1 × {p["name"]} to your cart.')
        await cart_cmd(update, context)

    elif data == "view_cart":
        await cart_cmd(update, context)

    elif data == "show_menu":
        await show_menu(update, context)

    elif data == "clear_cart":
        await clearcart(update, context)

    # start_checkout is now handled by ConversationHandler - removed unreachable code

# ---------- Checkout conversation ----------
async def checkout_start_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.from_user:
        return ConversationHandler.END
    await q.answer()
    uid = q.from_user.id
    if not CARTS.get(uid):
        await q.edit_message_text("Your cart is empty. Add items first.")
        return ConversationHandler.END
    await q.edit_message_text("Please reply with your delivery address (street, city, ZIP).")
    return ADDRESS

async def address_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ConversationHandler.END
    if context.user_data is not None:
        context.user_data["address"] = update.message.text.strip()
    await update.message.reply_text("Got it. Now reply with the best phone number for delivery.")
    return PHONE

async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not update.effective_user:
        return ConversationHandler.END
    if context.user_data is not None:
        context.user_data["phone"] = update.message.text.strip()
    uid = update.effective_user.id
    summary = cart_summary(uid)
    confirm_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirm & Send Order", callback_data="confirm_order")],
        [InlineKeyboardButton("Cancel Order", callback_data="cancel_order")]
    ])
    address = context.user_data.get('address', '') if context.user_data else ''
    phone = context.user_data.get('phone', '') if context.user_data else ''
    await update.message.reply_text(
        f"Please confirm your order:\n\n{summary}\n\n"
        f"Address:\n{address}\n"
        f"Phone: {phone}",
        reply_markup=confirm_kb
    )
    return ConversationHandler.END

# ---------- Order confirmation ----------
async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.from_user:
        return
    await q.answer()
    uid = q.from_user.id

    if q.data == "confirm_order":
        order = {
            "telegram_user": {"id": uid, "name": q.from_user.full_name, "username": q.from_user.username},
            "cart": CARTS.get(uid, []),
            "address": context.user_data.get("address", "(not provided)") if context.user_data else "(not provided)",
            "phone": context.user_data.get("phone", "(not provided)") if context.user_data else "(not provided)",
        }
        try:
            await send_order_email(order)
        except Exception:
            logger.exception("Failed to send order email")
            await q.edit_message_text("Failed to send order email. Contact the store directly.")
            return
        CARTS.pop(uid, None)
        # Clear user context data to prevent data leakage
        if context.user_data:
            context.user_data.clear()
        await q.edit_message_text("Order sent. The store will contact you for confirmation.")
    elif q.data == "cancel_order":
        # Clear user context data to prevent data leakage
        if context.user_data:
            context.user_data.clear()
        await q.edit_message_text("Order cancelled.")
        await show_menu(update, context)

# ---------- Email sending ----------
def build_email_body(order: dict) -> str:
    user = order["telegram_user"]
    cart_lines = [f'{it["qty"]} × {it["name"]} @ {format_price(it["price"])}' for it in order["cart"]]
    total = sum(it["qty"] * it["price"] for it in order["cart"])
    body = (
        f"New order from Telegram bot\n\n"
        f"From: {user.get('name', 'Unknown')} (@{user.get('username', 'None')})\n"
        f"Telegram ID: {user.get('id', 'Unknown')}\n\n"
        f"Delivery address:\n{order.get('address', 'Not provided')}\n\n"
        f"Contact phone: {order.get('phone', 'Not provided')}\n\n"
        f"Order items:\n" + "\n".join(cart_lines) + f"\n\nTotal: {format_price(total)}\n\n"
        "Please reply to this email or call to confirm payment/delivery."
    )
    return body

def send_order_email_sync(order: dict):
    """Synchronous email sending function to be run in thread executor."""
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP_USER or SMTP_PASSWORD not configured in secrets.")
    msg = EmailMessage()
    msg["Subject"] = f"Telegram Order from {order['telegram_user'].get('name')}"
    msg["From"] = SMTP_USER
    msg["To"] = LATE_NITE_EMAIL
    msg.set_content(build_email_body(order))

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
    logger.info("Order email sent to %s", LATE_NITE_EMAIL)

async def send_order_email(order: dict):
    """Async wrapper for email sending to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as executor:
        await loop.run_in_executor(executor, send_order_email_sync, order)

# ---------- Bot startup ----------
def main():
    logger.info("Starting Telegram bot...")
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    except Exception as e:
        # Sanitize error to prevent token exposure in logs
        if "Invalid token" in str(e) or "Unauthorized" in str(e):
            logger.error("Invalid Telegram token. Please check TELEGRAM_TOKEN in secrets.")
        else:
            logger.error("Failed to initialize bot: %s", type(e).__name__)
        raise SystemExit(1)

    # Add global error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Sorry, something went wrong. Please try again or contact support.")
    
    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", show_menu))

    # Checkout conversation (must be registered before general button handler)
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda update, context: checkout_start_conv(update, context), pattern="start_checkout")],
        states={
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, address_received)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_received)],
        },
        fallbacks=[],
        allow_reentry=True,
    )
    app.add_handler(conv)

    # Order confirmation handler (must be before general button handler)
    app.add_handler(CallbackQueryHandler(confirm_callback, pattern=r"^(confirm_order|cancel_order)$"))

    # General button handler (narrowed pattern to avoid conflicts)
    app.add_handler(CallbackQueryHandler(button_handler, pattern=r"^(add:|view_cart|show_menu|clear_cart)$"))

    try:
        app.run_polling()
    except Exception as e:
        # Sanitize error to prevent token exposure in logs
        if any(keyword in str(e).lower() for keyword in ["invalid token", "unauthorized", "forbidden", "token"]):
            logger.error("Authentication failed. Please check TELEGRAM_TOKEN in secrets.")
        else:
            logger.error("Bot startup failed: %s", type(e).__name__)
        raise SystemExit(1)

if __name__ == "__main__":
    main()