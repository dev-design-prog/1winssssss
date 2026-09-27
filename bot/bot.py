import asyncio
import logging
import os
import json
import random
import string
import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    WebAppInfo, LabeledPrice, Message
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from database_neon import Database
from config import BOT_TOKEN, NEON_CONNECTION_STRING, TELEGRAM_API_BASE, WEBAPP_URL

load_dotenv()

if not BOT_TOKEN or BOT_TOKEN.startswith("ВСТАВЬ_"):
    raise ValueError("❌ BOT_TOKEN не заполнен: открой bot/config.py и вставь токен бота.")
if not NEON_CONNECTION_STRING or NEON_CONNECTION_STRING.startswith("ВСТАВЬ_"):
    raise ValueError("❌ NEON_CONNECTION_STRING не заполнен: открой bot/config.py и вставь строку Neon.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_USERNAMES = ["Richie", "boros", "onewino"]

# Withdrawal notification message refs per request: request_id -> [(chat_id, message_id), ...]
withdrawal_admin_messages = {}
nft_admin_messages = {}  # request_id -> [(admin_chat_id, message_id), ...]

CHANNEL_URL = "https://catup.lol/onewinn"
NFT_PAYMENT_URL = "https://catup.lol/onewino"
CATUP_BOT_URL = "https://catup.lol/onewin_bot"

# Курс подарков: звёзды за подарок (можно менять)
GIFT_STAR_RATE = 1  # 1 звезда = 1 балансный балл

TERMS_TEXT = """📜 <b>Условия использования и правила бота:</b>

1. Администратор вправе изменить ваш баланс или очистить его без разглашения причин.
2. Запрещено использовать баги и недоработки казино для накрутки звёзд или получения других преимуществ.
3. При отправке NFT администратор вправе отказать в запросе, если количество звёзд слишком мало или слишком велико.
4. Администратор имеет право заблокировать вас или обнулить баланс за оскорбление казино или администратора.
5. Запрещено спамить запросами о пополнении через NFT-подарок, указывать неверную сумму или отправлять запрос без подарка. Администратор также вправе обнулить ваш баланс.
6. При заключении контракта с пиар-менеджером запрещено нарушать условия, например, прекращать рекламу до окончания срока контракта.
7. При пополнении баланса звёзды/NFT вам не вернут.
8. Вывод звёзд, полученных с помощью багов в боте, невозможен.
9. Подтверждая данный договор, вы автоматически подтверждаете его обновлённую версию, даже если мы не уведомляли вас об этом.
10. Если вы не согласны с данными правилами, просто заблокируйте бота или не подтверждайте договор."""

db = Database(NEON_CONNECTION_STRING)


class AdminStates(StatesGroup):
    waiting_add_balance_user = State()
    waiting_add_balance_amount = State()
    waiting_set_balance_user = State()
    waiting_set_balance_amount = State()
    waiting_unlock_withdraw_user = State()
    waiting_block_user = State()
    waiting_unblock_user = State()
    waiting_promo_name = State()
    waiting_promo_activations = State()
    waiting_promo_stars = State()
    waiting_broadcast_text = State()


class UserStates(StatesGroup):
    waiting_nft_amount = State()
    waiting_nft_confirm = State()
    waiting_nft_sent = State()
    waiting_stars_amount = State()
    waiting_activate_promo = State()


def is_admin(username: str) -> bool:
    if not username:
        return False
    return username.lower() in [a.lower() for a in ADMIN_USERNAMES]


def make_webapp_launch_token(user_id: int) -> str:
    """Short-lived signed token for Mini App launches that do not provide initData."""
    ts = int(time.time())
    payload = f"{int(user_id)}:{ts}"
    sig = hmac.new(BOT_TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}:{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def get_main_keyboard(user_id: int):
    launch_token = make_webapp_launch_token(user_id)
    webapp_url = f"{WEBAPP_URL}?user_id={user_id}&launch_token={launch_token}&v=20260913"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Открыть казино", web_app=WebAppInfo(url=webapp_url))],
        [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="deposit_menu")],
    ])
    return keyboard


# ============================================================
# УСЛОВИЯ ИСПОЛЬЗОВАНИЯ
# ============================================================

async def send_terms_request(message: types.Message):
    """Отправить приглашение принять условия использования"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Прочитать условия использования", callback_data="terms_read")],
        [InlineKeyboardButton(text="✅ Я принимаю условия, и я ознакомился с ними", callback_data="terms_accept")],
    ])
    await message.answer(
        "⚠️ <b>Прежде чем начать играть в боте, ознакомьтесь с правилами использования бота:</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def terms_read_handler(callback: types.CallbackQuery):
    """Показать текст условий использования"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="terms_back")],
    ])
    await callback.message.edit_text(
        TERMS_TEXT,
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


async def terms_back_handler(callback: types.CallbackQuery):
    """Вернуться к подтверждению условий"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Прочитать условия использования", callback_data="terms_read")],
        [InlineKeyboardButton(text="✅ Я принимаю условия, и я ознакомился с ними", callback_data="terms_accept")],
    ])
    await callback.message.edit_text(
        "⚠️ <b>Прежде чем начать играть в боте, ознакомьтесь с правилами использования бота:</b>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


async def terms_accept_handler(callback: types.CallbackQuery):
    """Пользователь принял условия — показать предложение подписаться на канал"""
    db.accept_terms(callback.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="terms_continue")],
    ])
    await callback.message.edit_text(
        "📢 <b>Прежде чем начать, подпишитесь на наш канал:</b>\n\n@casino",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


async def terms_continue_handler(callback: types.CallbackQuery):
    """После подписки — показать главное меню"""
    user = callback.from_user
    balance = db.get_balance(user.id)
    text = (
        f"🎰 <b>1Win</b>\n\n"
        f"Добро пожаловать, {user.first_name}!\n"
        f"Баланс: <b>{balance} ⭐</b>\n\n"
        f"📢 Подпишись на наш канал: {CHANNEL_URL}\n\n"
        f"Выбери действие:"
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=await get_main_keyboard(user.id)
    )
    await callback.answer()


async def check_terms_accepted(callback: types.CallbackQuery) -> bool:
    """Проверить принятие условий для callback-хендлеров. Возвращает True если можно продолжать."""
    if not db.has_accepted_terms(callback.from_user.id):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Прочитать условия использования", callback_data="terms_read")],
            [InlineKeyboardButton(text="✅ Я принимаю условия, и я ознакомился с ними", callback_data="terms_accept")],
        ])
        await callback.message.edit_text(
            "⚠️ <b>Прежде чем начать играть в боте, ознакомьтесь с правилами использования бота:</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.answer("❌ Сначала примите условия использования", show_alert=True)
        return False
    return True


async def start_handler(message: types.Message, state: FSMContext):
    user = message.from_user
    db.add_user(user.id, user.username or "", user.first_name or "")

    # Чёрный список: пользователь не получает меню, условия и кнопки Mini App.
    if db.is_user_blocked(user.id):
        await state.clear()
        await message.answer(
            "❌ <b>Вас заблокировал администратор.</b>\n\n"
            "Если у вас есть вопросы по этому поводу, обратитесь к администратору @onewino.",
            parse_mode="HTML"
        )
        return

    # Проверяем, принял ли пользователь условия использования
    if not db.has_accepted_terms(user.id):
        await send_terms_request(message)
        return

    balance = db.get_balance(user.id)
    start_payload = (message.text or "").split(maxsplit=1)[1].strip().lower() if (message.text or "").startswith("/start ") else ""
    if start_payload in {"deposit_nft", "nft"}:
        await state.set_state(UserStates.waiting_nft_amount)
        await message.answer(
            "🎁 <b>Пополнение NFT подарком</b>\n\n"
            "Введите количество звёзд, которые хотите пополнить:\n"
            "<i>(например: 500)</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Отмена", callback_data="deposit_menu")],
            ])
        )
        return

    if start_payload in {"deposit", "stars", "deposit_stars"}:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="100 ⭐", callback_data="pay_stars_100"), InlineKeyboardButton(text="300 ⭐", callback_data="pay_stars_300")],
            [InlineKeyboardButton(text="500 ⭐", callback_data="pay_stars_500"), InlineKeyboardButton(text="700 ⭐", callback_data="pay_stars_700")],
            [InlineKeyboardButton(text="1000 ⭐", callback_data="pay_stars_1000"), InlineKeyboardButton(text="2000 ⭐", callback_data="pay_stars_2000")],
            [InlineKeyboardButton(text="5000 ⭐", callback_data="pay_stars_5000")],
            [InlineKeyboardButton(text="✏️ Ввести своё количество", callback_data="pay_stars_custom")],
        ])
        await message.answer("⭐ <b>Сколько пополнить баланс?</b>\n\nВыберите сумму или введите своё количество:", parse_mode="HTML", reply_markup=keyboard)
        return

    text = (
        f"🎰 <b>1Win</b>\n\n"
        f"Добро пожаловать, {user.first_name}!\n"
        f"Баланс: <b>{balance} ⭐</b>\n\n"
        f"📢 Подпишись на наш канал: {CHANNEL_URL}\n\n"
        f"Выбери действие:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=await get_main_keyboard(user.id))


# ============================================================
# ПРИЁМ ПОДАРКОВ (Premium Gifts)
# ============================================================

async def gift_handler(message: types.Message, bot: Bot):
    """
    Обрабатывает входящие премиум-подарки (gift).
    Telegram присылает их как сервисное сообщение types.Message
    с полем message.gift (GiftInfo) начиная с Bot API 7.x
    """
    user = message.from_user
    if not user:
        return

    # Убедимся что пользователь есть в базе
    db.add_user(user.id, user.username or "", user.first_name or "")

    gift = message.gift  # GiftInfo object
    if gift is None:
        return

    # Звёздная стоимость подарка
    star_count = getattr(gift, "star_count", None) or getattr(gift.gift, "star_count", 0)

    if star_count and star_count > 0:
        credited = int(star_count * GIFT_STAR_RATE)
        db.add_balance(user.id, credited)
        new_balance = db.get_balance(user.id)

        # Уведомление отправителю
        try:
            await bot.send_message(
                user.id,
                f"🎁 <b>Подарок получен!</b>\n\n"
                f"За Premium-подарок тебе зачислено: <b>+{credited} ⭐</b>\n"
                f"Новый баланс: <b>{new_balance} ⭐</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify gift sender {user.id}: {e}")

        # Лог для админов
        logger.info(f"Gift from user {user.id} (@{user.username}): {star_count} stars → +{credited} balance")
    else:
        try:
            await bot.send_message(
                user.id,
                f"🎁 Подарок получен, но его стоимость не определена. "
                f"Обратись к администратору для зачисления.",
                parse_mode="HTML"
            )
        except Exception:
            pass


# ============================================================
# DEPOSIT MENU
# ============================================================

async def deposit_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    if not await check_terms_accepted(callback):
        return
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Пополнить звёздами", callback_data="deposit_stars")],
        [InlineKeyboardButton(text="🎁 Пополнить NFT подарком", callback_data="deposit_nft")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])
    await callback.message.edit_text(
        "💰 <b>Пополнение баланса</b>\n\nВыберите способ пополнения:",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


async def deposit_stars_handler(callback: types.CallbackQuery, bot: Bot):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="100 ⭐", callback_data="pay_stars_100"), InlineKeyboardButton(text="300 ⭐", callback_data="pay_stars_300")],
        [InlineKeyboardButton(text="500 ⭐", callback_data="pay_stars_500"), InlineKeyboardButton(text="700 ⭐", callback_data="pay_stars_700")],
        [InlineKeyboardButton(text="1000 ⭐", callback_data="pay_stars_1000"), InlineKeyboardButton(text="2000 ⭐", callback_data="pay_stars_2000")],
        [InlineKeyboardButton(text="5000 ⭐", callback_data="pay_stars_5000")],
        [InlineKeyboardButton(text="✏️ Ввести своё количество", callback_data="pay_stars_custom")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="deposit_menu")],
    ])
    await callback.message.edit_text(
        "⭐ <b>Пополнение звёздами</b>\n\nВыберите сумму или введите своё количество:",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


async def send_stars_invoice(bot: Bot, chat_id: int, user_id: int, amount: int):
    if amount < 1 or amount > 1000000:
        raise ValueError("invalid amount")
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Пополнение {amount} ⭐",
        description=f"Зачисление {amount} звёзд на баланс в 1Win Casino",
        payload=f"deposit_{user_id}_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} ⭐", amount=amount)],
    )


async def pay_stars_amount_handler(callback: types.CallbackQuery, bot: Bot):
    amount = int(callback.data.split("_")[2])
    await send_stars_invoice(bot, callback.message.chat.id, callback.from_user.id, amount)
    await callback.answer()


async def pay_stars_custom_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_stars_amount)
    await callback.message.answer(
        "✏️ Введите количество звёзд для пополнения.\nНапример: <b>1500</b>",
        parse_mode="HTML"
    )
    await callback.answer()


async def stars_amount_handler(message: types.Message, state: FSMContext, bot: Bot):
    try:
        amount = int(message.text.strip())
        if amount < 1 or amount > 1000000:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 1 до 1 000 000.")
        return
    await state.clear()
    await send_stars_invoice(bot, message.chat.id, message.from_user.id, amount)


async def pre_checkout_handler(pre_checkout_query: types.PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


async def successful_payment_handler(message: types.Message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    parts = payload.split("_")
    if len(parts) == 3 and parts[0] == "deposit":
        user_id = int(parts[1])
        amount = int(parts[2])
        db.add_balance(user_id, amount)
        db.track_deposit(user_id, amount)  # Отследить депозит
        new_balance = db.get_balance(user_id)
        await message.answer(
            f"✅ Оплата прошла успешно!\n"
            f"Зачислено: +{amount} ⭐\n"
            f"Баланс: {new_balance} ⭐"
        )


async def deposit_nft_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_nft_amount)
    await callback.message.edit_text(
        "🎁 <b>Пополнение NFT подарком</b>\n\n"
        "Введите количество звёзд, которые хотите пополнить:\n"
        "<i>(например: 500)</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="deposit_menu")],
        ])
    )
    await callback.answer()


async def nft_amount_handler(message: types.Message, state: FSMContext, bot: Bot):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное целое число (например: 500).")
        return

    await state.update_data(nft_amount=amount)
    await state.set_state(UserStates.waiting_nft_confirm)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="nft_confirm_yes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="deposit_menu"),
        ]
    ])
    await message.answer(
        f"🎁 <b>Подтверждение пополнения NFT</b>\n\n"
        f"Сумма: <b>{amount} ⭐</b>\n\n"
        f"Вы хотите пополнить баланс на <b>{amount} звёзд</b>?\n"
        f"После подтверждения вам нужно будет отправить NFT подарок администратору.",
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def nft_confirm_yes_handler(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data.get("nft_amount")
    if not amount:
        await callback.answer("❌ Сессия истекла, начните заново.", show_alert=True)
        await state.clear()
        return

    await state.update_data(nft_amount=amount)
    await state.set_state(UserStates.waiting_nft_sent)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я отправил NFT", callback_data="nft_sent")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="deposit_menu")],
    ])
    await callback.message.edit_text(
        f"🎁 <b>Отправьте NFT подарок</b>\n\n"
        f"Сумма: <b>{amount} ⭐</b>\n\n"
        f"1️⃣ Перейдите по ссылке: <a href=\"{NFT_PAYMENT_URL}\">@onewino</a>\n"
        f"2️⃣ Отправьте NFT подарок на указанную сумму\n"
        f"3️⃣ После отправки нажмите кнопку <b>«Я отправил NFT»</b>\n\n"
        f"⚠️ Не нажимайте кнопку пока не отправите подарок!",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()


async def nft_sent_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    amount = data.get("nft_amount")
    if not amount:
        await callback.answer("❌ Сессия истекла, начните заново.", show_alert=True)
        await state.clear()
        return

    user = callback.from_user
    request_id = db.create_nft_request(user.id, amount)
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_nft_{request_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_nft_{request_id}"),
        ]
    ])

    admin_text = (
        f"📬 <b>Новая заявка на пополнение NFT</b>\n\n"
        f"👤 Пользователь: <b>{user.first_name}</b>\n"
        f"🔗 Username: <b>@{user.username or '—'}</b>\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"💎 Сумма: <b>{amount} ⭐</b>\n"
        f"🧾 Заявка: <b>#{request_id}</b>"
    )

    refs = []
    for admin_username in ADMIN_USERNAMES:
        try:
            admin_id = await _find_admin_chat_id(bot, admin_username)
            if not admin_id:
                continue
            sent = await bot.send_message(admin_id, admin_text, parse_mode="HTML", reply_markup=keyboard)
            refs.append((admin_id, sent.message_id))
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_username}: {e}")

    if refs:
        nft_admin_messages[request_id] = refs

    await callback.message.edit_text(
        f"✅ <b>Заявка отправлена!</b>\n\n"
        f"Заявка <b>#{request_id}</b> на <b>{amount} ⭐</b> отправлена администраторам.\n"
        f"Ожидайте подтверждения — обычно это занимает несколько минут.",
        parse_mode="HTML"
    )
    await callback.answer()


async def _update_all_nft_admin_messages(bot: Bot, request_id: int, status_text: str):
    """Remove buttons and update final status in every admin's NFT notification."""
    refs = nft_admin_messages.pop(request_id, [])
    request = db.get_nft_request(request_id)
    if not request:
        return
    user_id = request["user_id"]
    amount = request["amount"]
    user = db.get_user(int(user_id)) or {}
    username = user.get("username") or "—"
    first_name = user.get("first_name") or ""

    final_text = (
        f"📬 <b>Заявка на пополнение NFT</b>\n\n"
        f"👤 Пользователь: <b>{first_name}</b>\n"
        f"🔗 Username: <b>@{username}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💎 Сумма: <b>{amount} ⭐</b>\n"
        f"🧾 Заявка: <b>#{request_id}</b>\n\n"
        f"{status_text}"
    )
    for (chat_id, message_id) in refs:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=final_text,
                parse_mode="HTML",
                reply_markup=None
            )
        except Exception as e:
            logger.warning(f"Could not update NFT admin msg {message_id} in {chat_id}: {e}")


async def approve_nft_handler(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    request_id = int(callback.data.split("_")[2])
    request = db.get_nft_request(request_id)

    if not request:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    if request["status"] != "pending":
        await callback.answer("⚠️ Заявка уже обработана другим админом", show_alert=True)
        return

    user_id = request["user_id"]
    amount = request["amount"]
    db.update_nft_request_status(request_id, "approved")
    db.add_balance(user_id, amount)
    db.track_deposit(user_id, amount)
    new_balance = db.get_balance(user_id)

    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Заявка #{request_id} одобрена!</b>\n\n"
            f"Зачислено: <b>+{amount} ⭐</b>\n"
            f"Текущий баланс: <b>{new_balance} ⭐</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")

    await _update_all_nft_admin_messages(
        bot, request_id,
        f"✅ <b>Одобрено</b> администратором @{callback.from_user.username}"
    )
    await callback.answer("✅ Баланс зачислен")


async def reject_nft_handler(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    request_id = int(callback.data.split("_")[2])
    request = db.get_nft_request(request_id)

    if not request:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    if request["status"] != "pending":
        await callback.answer("⚠️ Заявка уже обработана другим админом", show_alert=True)
        return

    db.update_nft_request_status(request_id, "rejected")

    try:
        await bot.send_message(
            request["user_id"],
            f"❌ <b>Заявка #{request_id} отклонена.</b>\n\n"
            f"Если вы считаете это ошибкой — обратитесь к администратору.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await _update_all_nft_admin_messages(
        bot, request_id,
        f"❌ <b>Отклонено</b> администратором @{callback.from_user.username}"
    )
    await callback.answer("❌ Заявка отклонена")


async def back_main_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user = callback.from_user
    balance = db.get_balance(user.id)
    text = (
        f"🎰 <b>1Win</b>\n\n"
        f"Баланс: <b>{balance} ⭐</b>\n\nВыбери действие:"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=await get_main_keyboard(user.id))
    await callback.answer()


async def _find_admin_chat_id(bot: Bot, admin_username: str):
    """Resolve an admin chat id from DB, falling back to Telegram username lookup."""
    admin_id = db.get_user_id_by_username(admin_username)
    if admin_id:
        return int(admin_id)
    try:
        chat = await bot.get_chat(f"@{admin_username.lstrip('@')}")
        return int(chat.id)
    except Exception as e:
        logger.warning(f"Cannot resolve admin @{admin_username}: {e}")
        return None


async def notify_pending_withdrawals(bot: Bot):
    """Periodically notify all configured admins about new pending withdrawals."""
    notified = set()
    while True:
        try:
            pending = db.get_pending_withdrawals()
            for req in pending:
                request_id = int(req["id"])
                if request_id in notified:
                    continue
                user = db.get_user(int(req["user_id"])) or {}
                username = user.get("username") or "без username"
                first_name = user.get("first_name") or ""
                amount = int(req["amount"])
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_withdraw_{request_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_withdraw_{request_id}"),
                ]])
                text = (
                    f"📤 <b>Новая заявка на вывод</b>\n\n"
                    f"👤 Пользователь: <b>{first_name}</b>\n"
                    f"🔗 Username: <b>@{username}</b>\n"
                    f"🆔 ID: <code>{req['user_id']}</code>\n"
                    f"💰 Сумма: <b>{amount} ⭐</b>\n"
                    f"🧾 Заявка: <b>#{request_id}</b>"
                )
                refs = []
                for admin_username in ADMIN_USERNAMES:
                    try:
                        admin_id = await _find_admin_chat_id(bot, admin_username)
                        if not admin_id:
                            continue
                        sent_message = await bot.send_message(
                            admin_id, text, parse_mode="HTML", reply_markup=keyboard
                        )
                        refs.append((admin_id, sent_message.message_id))
                    except Exception as e:
                        logger.error(f"Failed to notify @{admin_username} about withdrawal #{request_id}: {e}")
                if refs:
                    withdrawal_admin_messages[request_id] = refs
                    notified.add(request_id)
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Withdrawal notifier error: {e}")
            await asyncio.sleep(5)


async def _update_all_admin_withdrawal_messages(bot: Bot, request_id: int, status_text: str):
    """Remove buttons and show the same final status in every admin's notification."""
    refs = withdrawal_admin_messages.pop(request_id, [])
    req = db.get_withdrawal_request(request_id)
    if not req:
        return
    user = db.get_user(int(req["user_id"])) or {}
    username = user.get("username") or "без username"
    first_name = user.get("first_name") or ""
    amount = int(req["amount"])
    final_text = (
        f"📤 <b>Заявка на вывод</b>\n\n"
        f"👤 Пользователь: <b>{first_name}</b>\n"
        f"🔗 Username: <b>@{username}</b>\n"
        f"🆔 ID: <code>{req['user_id']}</code>\n"
        f"💰 Сумма: <b>{amount} ⭐</b>\n"
        f"🧾 Заявка: <b>#{request_id}</b>\n\n"
        f"{status_text}"
    )
    for chat_id, message_id in refs:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=final_text,
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception as e:
            logger.warning(f"Failed to update admin notification #{request_id} in {chat_id}: {e}")


async def approve_withdraw_handler(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    request_id = int(callback.data.rsplit("_", 1)[1])
    req = db.get_withdrawal_request(request_id)
    if not req or req.get("status") != "pending":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    # Только один администратор может обработать pending-заявку.
    # Повторный клик/старое сообщение ничего не меняет.
    if not db.update_withdrawal_status(request_id, "approved"):
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    await _update_all_admin_withdrawal_messages(
        bot, request_id,
        f"✅ <b>Одобрено</b> @{callback.from_user.username or 'администратором'}"
    )
    try:
        await bot.send_message(
            int(req["user_id"]),
            f"✅ <b>Заявка на вывод #{request_id} одобрена!</b>\n\n"
            f"💰 Сумма: {int(req['amount'])} ⭐\n\n"
            f"📩 Для получения выплаты вам нужно написать по юзу <b>@onewino</b>."
        )
    except Exception:
        pass
    await callback.answer("✅ Заявка одобрена")


async def reject_withdraw_handler(callback: types.CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    request_id = int(callback.data.rsplit("_", 1)[1])
    req = db.get_withdrawal_request(request_id)
    if not req or req.get("status") != "pending":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    # Возврат звёзд выполняется ТОЛЬКО если именно этот клик успешно
    # перевёл заявку pending -> rejected. Повторный отказ не вернёт звёзды ещё раз.
    if not db.update_withdrawal_status(request_id, "rejected"):
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    amount = int(req["amount"])
    db.add_balance(int(req["user_id"]), amount)
    await _update_all_admin_withdrawal_messages(
        bot, request_id,
        f"❌ <b>Отказано</b> @{callback.from_user.username or 'администратором'}"
    )
    try:
        await bot.send_message(
            int(req["user_id"]),
            f"❌ Заявка на вывод #{request_id} отклонена.\n"
            f"{amount} ⭐ возвращены на баланс."
        )
    except Exception:
        pass
    await callback.answer("❌ Заявка отклонена")


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Пополнить баланс пользователю", callback_data="admin_add_balance")],
        [InlineKeyboardButton(text="🎯 Установить точный баланс", callback_data="admin_set_balance")],
        [InlineKeyboardButton(text="💎 Выдать себе баланс", callback_data="admin_self_balance")],
        [InlineKeyboardButton(text="🧹 Сбросить баланс у всех", callback_data="admin_reset_all_balances")],
        [InlineKeyboardButton(text="🔓 Разблокировать вывод пользователю", callback_data="admin_unlock_withdraw")],
        [InlineKeyboardButton(text="🚫 Добавить в чёрный список", callback_data="admin_block_user")],
        [InlineKeyboardButton(text="✅ Снять бан с пользователя", callback_data="admin_unblock_user")],
        [InlineKeyboardButton(text="🎟 Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos")],
        [InlineKeyboardButton(text="📣 Рассылка всем", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📋 Разослать правила всем (принудительно)", callback_data="admin_broadcast_terms")],
    ])


async def admin_handler(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.answer("❌ У вас нет доступа к админ-панели.")
        return

    await message.answer(
        "🔧 <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


async def admin_add_balance_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_add_balance_user)
    await callback.message.edit_text("👤 Введите @username пользователя для пополнения баланса:")
    await callback.answer()


async def admin_add_balance_user_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    username = message.text.strip().lstrip("@")
    await state.update_data(target_username=username)
    await state.set_state(AdminStates.waiting_add_balance_amount)
    await message.answer(f"💰 Введите сумму для @{username}:")


async def admin_add_balance_amount_handler(message: types.Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.username):
        return
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число.")
        return

    data = await state.get_data()
    username = data["target_username"]
    user_id = db.get_user_id_by_username(username)

    if not user_id:
        await message.answer(f"❌ Пользователь @{username} не найден в базе.")
        await state.clear()
        return

    db.add_balance(user_id, amount)
    new_balance = db.get_balance(user_id)
    await state.clear()

    try:
        await bot.send_message(user_id, f"✅ Тебе зачислено +{amount}⭐\nБаланс: {new_balance}⭐")
    except Exception as e:
        logger.error(f"Failed to notify user: {e}")

    await message.answer(
        f"✅ Пользователю @{username} зачислено {amount} ⭐\n"
        f"Новый баланс: {new_balance} ⭐"
    )


async def admin_set_balance_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_set_balance_user)
    await callback.message.edit_text("👤 Введите @username пользователя, которому нужно установить баланс:")
    await callback.answer()


async def admin_set_balance_user_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    username = message.text.strip().lstrip("@")
    user_id = db.get_user_id_by_username(username)
    if not user_id:
        await message.answer(f"❌ Пользователь @{username} не найден в базе.")
        await state.clear()
        return
    await state.update_data(target_username=username, target_user_id=user_id)
    await state.set_state(AdminStates.waiting_set_balance_amount)
    current = db.get_balance(user_id)
    await message.answer(f"🎯 Текущий баланс @{username}: {current} ⭐\nВведите новый точный баланс:")


async def admin_set_balance_amount_handler(message: types.Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.username):
        return
    try:
        amount = int(message.text.strip())
        if amount < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число от 0 и выше.")
        return
    data = await state.get_data()
    user_id = data.get("target_user_id")
    username = data.get("target_username", "")
    if not user_id or not db.set_balance(user_id, amount):
        await message.answer("❌ Не удалось установить баланс.")
        await state.clear()
        return
    await state.clear()
    try:
        await bot.send_message(user_id, f"ℹ️ Администратор установил твой баланс: {amount} ⭐")
    except Exception as e:
        logger.error(f"Failed to notify user: {e}")
    await message.answer(f"✅ Баланс @{username} установлен: {amount} ⭐")


async def admin_reset_all_balances_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ ДА, СБРОСИТЬ ВСЕМ", callback_data="admin_reset_all_balances_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_reset_all_balances_cancel"),
        ]
    ])
    await callback.message.edit_text(
        "⚠️ <b>Сбросить баланс у всех пользователей?</b>\n\n"
        "Баланс звёзд у всех будет установлен в <b>0 ⭐</b>.\n"
        "История игр, депозиты и заявки не удаляются.\n\n"
        "Это действие нельзя отменить.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


async def admin_reset_all_balances_confirm_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    result = db.reset_all_balances()
    if result.get("error"):
        await callback.message.edit_text(
            "❌ <b>Не удалось сбросить балансы.</b>\n\nПопробуйте ещё раз.",
            parse_mode="HTML",
            reply_markup=admin_keyboard(),
        )
        await callback.answer("Ошибка базы данных", show_alert=True)
        return

    await callback.message.edit_text(
        "✅ <b>Баланс сброшен у всех пользователей.</b>\n\n"
        f"👤 Пользователей с ненулевым балансом: <b>{result['users']}</b>\n"
        f"⭐ Списано со счетов: <b>{result['stars']}</b>\n"
        "Новый баланс: <b>0 ⭐</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )
    await callback.answer("Баланс у всех сброшен", show_alert=True)


async def admin_reset_all_balances_cancel_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🔧 <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )
    await callback.answer("Отменено")


async def admin_self_balance_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.update_data(target_username=callback.from_user.username)
    await state.set_state(AdminStates.waiting_add_balance_amount)
    await callback.message.edit_text("💰 Введите сумму для зачисления себе:")
    await callback.answer()


async def admin_block_user_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_block_user)
    await callback.message.edit_text(
        "🚫 <b>Добавление в чёрный список</b>\n\n"
        "Введите @username или Telegram ID пользователя:",
        parse_mode="HTML"
    )
    await callback.answer()


async def admin_block_user_handler_message(message: types.Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.username):
        return
    value = message.text.strip().lstrip("@") if message.text else ""
    user_id = None
    if value.isdigit():
        user_id = int(value)
        if not db.get_user(user_id):
            user_id = None
    else:
        user_id = db.get_user_id_by_username(value)

    if not user_id:
        await message.answer("❌ Пользователь не найден в базе. Введите @username или Telegram ID.")
        return
    if db.is_user_blocked(user_id):
        await state.clear()
        await message.answer("ℹ️ Этот пользователь уже находится в чёрном списке.", reply_markup=admin_keyboard())
        return

    username = (db.get_user(user_id) or {}).get("username", "")
    if not db.block_user(user_id, message.from_user.username or ""):
        await state.clear()
        await message.answer("❌ Не удалось заблокировать пользователя.", reply_markup=admin_keyboard())
        return
    await state.clear()
    try:
        await bot.send_message(
            user_id,
            "❌ <b>Вас заблокировал администратор.</b>\n\n"
            "Если у вас есть вопросы по этому поводу, обратитесь к администратору @onewino.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Failed to notify blocked user {user_id}: {e}")
    label = f"@{username}" if username else str(user_id)
    await message.answer(f"🚫 Пользователь {label} добавлен в чёрный список.", reply_markup=admin_keyboard())


async def admin_unblock_user_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_unblock_user)
    await callback.message.edit_text(
        "✅ <b>Снятие бана</b>\n\n"
        "Введите @username или Telegram ID пользователя:",
        parse_mode="HTML"
    )
    await callback.answer()


async def admin_unblock_user_handler_message(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    value = message.text.strip().lstrip("@") if message.text else ""
    user_id = int(value) if value.isdigit() else db.get_user_id_by_username(value)
    if not user_id or not db.get_user(user_id):
        await message.answer("❌ Пользователь не найден в базе. Введите @username или Telegram ID.")
        return
    if not db.is_user_blocked(user_id):
        await state.clear()
        await message.answer("ℹ️ Этот пользователь не заблокирован.", reply_markup=admin_keyboard())
        return

    if not db.unblock_user(user_id):
        await state.clear()
        await message.answer("❌ Не удалось снять бан.", reply_markup=admin_keyboard())
        return
    await state.clear()
    try:
        await bot.send_message(user_id, "✅ <b>Администратор снял с вас блокировку.</b>\n\nТеперь вы снова можете пользоваться ботом.", parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to notify unblocked user {user_id}: {e}")
    user = db.get_user(user_id) or {}
    label = f"@{user.get('username')}" if user.get('username') else str(user_id)
    await message.answer(f"✅ Бан с пользователя {label} снят.", reply_markup=admin_keyboard())


async def admin_unlock_withdraw_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_unlock_withdraw_user)
    await callback.message.edit_text("👤 Введите @username пользователя для разблокировки вывода:")
    await callback.answer()


async def admin_unlock_withdraw_user_handler(message: types.Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.username):
        return
    username = message.text.strip().lstrip("@")
    user_id = db.get_user_id_by_username(username)

    if not user_id:
        await message.answer(f"❌ Пользователь @{username} не найден в базе.")
        await state.clear()
        return

    unlocked = db.unlock_withdraw_for_user(user_id)
    await state.clear()
    if not unlocked:
        await message.answer(f"❌ Не удалось разблокировать вывод: пользователь @{username} не найден.")
        return

    try:
        await bot.send_message(user_id, f"✅ Ограничение на вывод снято. Ты можешь выводить баланс без 1-часового ожидания.")
    except Exception as e:
        logger.error(f"Failed to notify user: {e}")

    await message.answer(
        f"✅ Пользователю @{username} разблокирован вывод баланса"
    )


async def admin_create_promo_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_promo_name)
    await callback.message.edit_text(
        "🎟 <b>Создание промокода</b>\n\nВведите название (только буквы и цифры):",
        parse_mode="HTML"
    )
    await callback.answer()


async def admin_promo_name_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    name = message.text.strip().upper()
    if not name.isalnum():
        await message.answer("❌ Промокод должен содержать только буквы и цифры.")
        return
    if db.promo_exists(name):
        await message.answer("❌ Промокод с таким именем уже существует.")
        return
    await state.update_data(promo_name=name)
    await state.set_state(AdminStates.waiting_promo_activations)
    await message.answer(f"🔢 Промокод: <b>{name}</b>\n\nВведите количество активаций:", parse_mode="HTML")


async def admin_promo_activations_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    try:
        activations = int(message.text.strip())
        if activations <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число.")
        return
    await state.update_data(promo_activations=activations)
    await state.set_state(AdminStates.waiting_promo_stars)
    await message.answer("⭐ Введите количество звёзд которые даёт промокод:")


async def admin_promo_stars_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    try:
        stars = int(message.text.strip())
        if stars <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число.")
        return

    data = await state.get_data()
    name = data["promo_name"]
    activations = data["promo_activations"]
    db.create_promo(name, activations, stars)
    await state.clear()

    await message.answer(
        f"✅ <b>Промокод создан!</b>\n\n"
        f"Код: <code>{name}</code>\n"
        f"Активаций: {activations}\n"
        f"Даёт: {stars} ⭐",
        parse_mode="HTML"
    )


async def admin_list_promos_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    promos = db.get_all_promos()
    if not promos:
        await callback.message.edit_text(
            "📋 Промокодов нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
            ])
        )
        await callback.answer()
        return

    text = "📋 <b>Список промокодов:</b>\n\n"
    for p in promos:
        text += f"• <code>{p['name']}</code> — {p['stars']}⭐ (активаций: {p['used']}/{p['max_activations']})\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ============================================================
# РАССЫЛКА ВСЕМ
# ============================================================

async def admin_broadcast_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    users_count = db.get_all_users_count()
    await state.set_state(AdminStates.waiting_broadcast_text)
    await callback.message.edit_text(
        f"📣 <b>Рассылка</b>\n\n"
        f"Пользователей в базе: <b>{users_count}</b>\n\n"
        f"Напишите текст сообщения для рассылки.\n"
        f"Поддерживается HTML-разметка (<b>bold</b>, <i>italic</i>, <code>code</code>).\n\n"
        f"Или отправьте /cancel для отмены.",
        parse_mode="HTML"
    )
    await callback.answer()


async def admin_broadcast_text_handler(message: types.Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.username):
        return

    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=admin_keyboard())
        return

    broadcast_text = message.html_text if message.text else None
    if not broadcast_text:
        await message.answer("❌ Отправьте текстовое сообщение.")
        return

    await state.clear()

    all_user_ids = db.get_all_user_ids()
    total = len(all_user_ids)

    status_msg = await message.answer(
        f"📣 <b>Рассылка запущена...</b>\n\n"
        f"Всего пользователей: {total}\n"
        f"Отправлено: 0 / {total}",
        parse_mode="HTML"
    )

    sent = 0
    failed = 0

    for i, uid in enumerate(all_user_ids):
        try:
            await bot.send_message(
                uid,
                f"📣 <b>Сообщение от администрации 1Win</b>\n\n{broadcast_text}",
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Broadcast failed for user {uid}: {e}")
            failed += 1

        # Обновляем статус каждые 20 пользователей
        if (i + 1) % 20 == 0 or (i + 1) == total:
            try:
                await status_msg.edit_text(
                    f"📣 <b>Рассылка...</b>\n\n"
                    f"Отправлено: {sent} / {total}\n"
                    f"Ошибок: {failed}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        # Небольшая задержка чтобы не словить flood
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Всего: {total}\n"
        f"Успешно: {sent}\n"
        f"Не доставлено: {failed}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В панель", callback_data="admin_back")]
        ])
    )


async def admin_broadcast_terms_handler(callback: types.CallbackQuery, bot: Bot):
    """Разослать правила всем пользователям и сбросить флаг принятия, чтобы они не могли продолжить без подтверждения"""
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    all_user_ids = db.get_all_user_ids()
    total = len(all_user_ids)

    # Сбросить флаг принятия условий для ВСЕХ пользователей
    db.reset_all_terms_accepted()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Прочитать условия использования", callback_data="terms_read")],
        [InlineKeyboardButton(text="✅ Я принимаю условия, и я ознакомился с ними", callback_data="terms_accept")],
    ])
    broadcast_msg = "⚠️ <b>Прежде чем начать играть в боте, ознакомьтесь с правилами использования бота:</b>"

    status_msg = await callback.message.edit_text(
        f"📋 <b>Рассылка правил запущена...</b>\n\n"
        f"Всего пользователей: {total}\n"
        f"Отправлено: 0 / {total}",
        parse_mode="HTML"
    )
    await callback.answer()

    sent = 0
    failed = 0

    for i, uid in enumerate(all_user_ids):
        try:
            await bot.send_message(
                uid,
                broadcast_msg,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Terms broadcast failed for user {uid}: {e}")
            failed += 1

        if (i + 1) % 20 == 0 or (i + 1) == total:
            try:
                await status_msg.edit_text(
                    f"📋 <b>Рассылка правил...</b>\n\n"
                    f"Отправлено: {sent} / {total}\n"
                    f"Ошибок: {failed}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Рассылка правил завершена!</b>\n\n"
        f"Всего: {total}\n"
        f"Успешно: {sent}\n"
        f"Не доставлено: {failed}\n\n"
        f"⚠️ Все пользователи должны заново принять условия.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В панель", callback_data="admin_back")]
        ])
    )


async def admin_back_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🔧 <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
    await callback.answer()


# ============================================================
# MAIN
# ============================================================

async def main():
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer

    bot = Bot(
        token=BOT_TOKEN,
        session=AiohttpSession(
            api=TelegramAPIServer.from_base(TELEGRAM_API_BASE)
        )
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Background notifier for withdrawal requests
    withdrawal_notifier_task = asyncio.create_task(notify_pending_withdrawals(bot))

    # Basic commands
    dp.message.register(start_handler, Command("start"))
    dp.message.register(admin_handler, Command("admin"))

    # Gift handler — срабатывает на сервисные сообщения с подарком
    dp.message.register(gift_handler, F.gift.as_("gift"))

    # Callback handlers
    dp.callback_query.register(deposit_menu_handler, F.data == "deposit_menu")
    dp.callback_query.register(deposit_stars_handler, F.data == "deposit_stars")
    dp.callback_query.register(pay_stars_amount_handler, F.data.regexp(r"^pay_stars_(100|300|500|700|1000|2000|5000)$"))
    dp.callback_query.register(pay_stars_custom_handler, F.data == "pay_stars_custom")
    dp.callback_query.register(deposit_nft_handler, F.data == "deposit_nft")
    dp.callback_query.register(nft_confirm_yes_handler, F.data == "nft_confirm_yes")
    dp.callback_query.register(nft_sent_handler, F.data == "nft_sent")
    dp.callback_query.register(approve_nft_handler, F.data.startswith("approve_nft_"))
    dp.callback_query.register(reject_nft_handler, F.data.startswith("reject_nft_"))
    dp.callback_query.register(back_main_handler, F.data == "back_main")

    # Admin callbacks
    dp.callback_query.register(admin_add_balance_handler, F.data == "admin_add_balance")
    dp.callback_query.register(admin_set_balance_handler, F.data == "admin_set_balance")
    dp.callback_query.register(admin_self_balance_handler, F.data == "admin_self_balance")
    dp.callback_query.register(admin_reset_all_balances_handler, F.data == "admin_reset_all_balances")
    dp.callback_query.register(admin_reset_all_balances_confirm_handler, F.data == "admin_reset_all_balances_confirm")
    dp.callback_query.register(admin_reset_all_balances_cancel_handler, F.data == "admin_reset_all_balances_cancel")
    dp.callback_query.register(admin_unlock_withdraw_handler, F.data == "admin_unlock_withdraw")
    dp.callback_query.register(admin_block_user_handler, F.data == "admin_block_user")
    dp.callback_query.register(admin_unblock_user_handler, F.data == "admin_unblock_user")
    dp.callback_query.register(admin_create_promo_handler, F.data == "admin_create_promo")
    dp.callback_query.register(admin_list_promos_handler, F.data == "admin_list_promos")
    dp.callback_query.register(admin_broadcast_handler, F.data == "admin_broadcast")
    dp.callback_query.register(admin_broadcast_terms_handler, F.data == "admin_broadcast_terms")
    dp.callback_query.register(terms_read_handler, F.data == "terms_read")
    dp.callback_query.register(terms_back_handler, F.data == "terms_back")
    dp.callback_query.register(terms_accept_handler, F.data == "terms_accept")
    dp.callback_query.register(terms_continue_handler, F.data == "terms_continue")
    dp.callback_query.register(admin_back_handler, F.data == "admin_back")
    dp.callback_query.register(approve_withdraw_handler, F.data.startswith("approve_withdraw_"))
    dp.callback_query.register(reject_withdraw_handler, F.data.startswith("reject_withdraw_"))

    # State handlers
    dp.message.register(nft_amount_handler, UserStates.waiting_nft_amount)
    dp.message.register(stars_amount_handler, UserStates.waiting_stars_amount)
    dp.message.register(admin_add_balance_user_handler, AdminStates.waiting_add_balance_user)
    dp.message.register(admin_add_balance_amount_handler, AdminStates.waiting_add_balance_amount)
    dp.message.register(admin_set_balance_user_handler, AdminStates.waiting_set_balance_user)
    dp.message.register(admin_set_balance_amount_handler, AdminStates.waiting_set_balance_amount)
    dp.message.register(admin_unlock_withdraw_user_handler, AdminStates.waiting_unlock_withdraw_user)
    dp.message.register(admin_block_user_handler_message, AdminStates.waiting_block_user)
    dp.message.register(admin_unblock_user_handler_message, AdminStates.waiting_unblock_user)
    dp.message.register(admin_promo_name_handler, AdminStates.waiting_promo_name)
    dp.message.register(admin_promo_activations_handler, AdminStates.waiting_promo_activations)
    dp.message.register(admin_promo_stars_handler, AdminStates.waiting_promo_stars)
    dp.message.register(admin_broadcast_text_handler, AdminStates.waiting_broadcast_text)

    # Payment handlers
    dp.pre_checkout_query.register(pre_checkout_handler)
    dp.message.register(successful_payment_handler, F.successful_payment)

    logger.info("Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
