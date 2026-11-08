import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# Token: set TELEGRAM_BOT_TOKEN env var or replace the placeholder below.
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9105200978:AAHIbdLEuTZIGwdVb6OGHwqRBbgzBsjLZX8")
ADMIN_PASSWORD = "70078181As"
CONFIG_PATH = Path(__file__).with_name("config.json")

logging.basicFormat = None
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


class AdminStates(StatesGroup):
    waiting_password = State()
    panel = State()
    edit_start_text = State()
    add_required_channel = State()
    remove_required_channel = State()
    set_destination = State()


def default_config() -> dict[str, Any]:
    return {
        "start_text": "Привет! Нажмите кнопку ниже, чтобы продолжить.",
        "required_channels": [],  # [{"chat_id": "@channel", "title": "...", "url": "https://t.me/..."}]
        "destination_channel": None,  # {"chat_id": "@channel", "title": "...", "url": "https://t.me/..."}
    }


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read config.json, using defaults")
        return default_config()
    cfg = default_config()
    cfg.update({k: v for k, v in data.items() if k in cfg})
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_channel_input(text: str) -> dict[str, str] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Accept: @username, username, https://t.me/username, t.me/+invite, -100...
    url = raw
    chat_id = raw
    title = raw
    if raw.startswith("https://") or raw.startswith("http://") or raw.startswith("t.me/"):
        if raw.startswith("t.me/"):
            url = "https://" + raw
        if "/+" in url or "joinchat" in url:
            # Invite links can't be checked via get_chat_member by link alone reliably;
            # keep as URL button, but membership checks may require chat id.
            chat_id = url
            title = "Канал"
        else:
            username = url.rstrip("/").split("/")[-1].split("?")[0]
            if username and not username.startswith("+"):
                chat_id = "@" + username.lstrip("@")
                title = chat_id
                url = f"https://t.me/{username.lstrip('@')}"
    elif raw.startswith("@"):  # username
        chat_id = raw
        title = raw
        url = f"https://t.me/{raw.lstrip('@')}"
    elif raw.lstrip("-").isdigit():
        chat_id = raw
        title = raw
        url = ""
    else:
        # treat as username
        chat_id = "@" + raw
        title = chat_id
        url = f"https://t.me/{raw}"
    return {"chat_id": chat_id, "title": title, "url": url}


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить текст /start", callback_data="adm:edit_start")],
            [InlineKeyboardButton(text="Добавить обязательный канал", callback_data="adm:add_req")],
            [InlineKeyboardButton(text="Удалить обязательный канал", callback_data="adm:del_req")],
            [InlineKeyboardButton(text="Список обязательных каналов", callback_data="adm:list_req")],
            [InlineKeyboardButton(text="Установить канал назначения", callback_data="adm:set_dest")],
            [InlineKeyboardButton(text="Показать настройки", callback_data="adm:show")],
            [InlineKeyboardButton(text="Выйти", callback_data="adm:exit")],
        ]
    )


async def is_subscribed(bot: Bot, user_id: int, chat_id: str) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as e:
        logger.warning("Subscription check failed for %s: %s", chat_id, e)
        return False
    status = getattr(member, "status", "")
    return status not in {"left", "kicked"}


def subscription_kb(cfg: dict[str, Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ch in cfg.get("required_channels", []):
        title = ch.get("title") or ch.get("chat_id") or "Канал"
        url = ch.get("url") or ""
        if not url and str(ch.get("chat_id", "")).startswith("@"):
            url = f"https://t.me/{str(ch['chat_id']).lstrip('@')}"
        if url:
            rows.append([InlineKeyboardButton(text=f"Подписаться: {title}", url=url)])
        else:
            rows.append([InlineKeyboardButton(text=f"Канал: {title}", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="Проверить подписку", callback_data="sub:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def destination_kb(cfg: dict[str, Any]) -> InlineKeyboardMarkup | None:
    dest = cfg.get("destination_channel")
    if not dest:
        return None
    title = dest.get("title") or "Перейти в канал"
    url = dest.get("url") or ""
    if not url and str(dest.get("chat_id", "")).startswith("@"):
        url = f"https://t.me/{str(dest['chat_id']).lstrip('@')}"
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=title, url=url)]]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    cfg = load_config()
    text = cfg.get("start_text") or "Привет!"
    required = cfg.get("required_channels") or []
    if not required:
        kb = destination_kb(cfg)
        await message.answer(text, reply_markup=kb)
        return
    await message.answer(
        text + "\n\nЧтобы продолжить, подпишитесь на каналы ниже и нажмите «Проверить подписку».",
        reply_markup=subscription_kb(cfg),
    )


@router.callback_query(F.data == "sub:check")
async def check_subscription(callback: CallbackQuery, bot: Bot) -> None:
    cfg = load_config()
    required = cfg.get("required_channels") or []
    if not required:
        kb = destination_kb(cfg)
        await callback.message.answer(
            cfg.get("start_text") or "Готово.",
            reply_markup=kb,
        )
        await callback.answer()
        return
    not_ok = []
    for ch in required:
        chat_id = ch.get("chat_id")
        if not chat_id:
            continue
        ok = await is_subscribed(bot, callback.from_user.id, chat_id)
        if not ok:
            not_ok.append(ch.get("title") or chat_id)
    if not_ok:
        await callback.answer("Вы подписаны не на все каналы.", show_alert=True)
        await callback.message.answer(
            "Не найдена подписка на: " + ", ".join(not_ok) + "\nПодпишитесь и нажмите проверку снова.",
            reply_markup=subscription_kb(cfg),
        )
        return
    kb = destination_kb(cfg)
    if kb:
        await callback.message.answer("Подписка подтверждена! Можете перейти:", reply_markup=kb)
    else:
        await callback.message.answer("Подписка подтверждена! Канал назначения пока не настроен администратором.")
    await callback.answer("Подписка подтверждена")


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminStates.waiting_password)
    await message.answer("Введите пароль администратора:")


@router.message(AdminStates.waiting_password)
async def admin_password(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip() != ADMIN_PASSWORD:
        await message.answer("Неверный пароль. Попробуйте ещё раз или отправьте /start для выхода.")
        return
    await state.set_state(AdminStates.panel)
    await message.answer("Админ-панель:", reply_markup=admin_panel_kb())


@router.callback_query(AdminStates.panel, F.data == "adm:exit")
async def admin_exit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Вы вышли из админ-панели.")
    await callback.answer()


@router.callback_query(AdminStates.panel, F.data == "adm:show")
async def admin_show(callback: CallbackQuery) -> None:
    cfg = load_config()
    req = cfg.get("required_channels") or []
    dest = cfg.get("destination_channel")
    lines = [
        "Текущие настройки:",
        f"Текст /start: {cfg.get('start_text', '')}",
        f"Обязательных каналов: {len(req)}",
    ]
    for i, ch in enumerate(req, 1):
        lines.append(f"  {i}. {ch.get('title')} ({ch.get('chat_id')}) {ch.get('url','')}")
    if dest:
        lines.append(f"Канал назначения: {dest.get('title')} ({dest.get('chat_id')}) {dest.get('url','')}")
    else:
        lines.append("Канал назначения: не задан")
    await callback.message.answer("\n".join(lines), reply_markup=admin_panel_kb())
    await callback.answer()


@router.callback_query(AdminStates.panel, F.data == "adm:edit_start")
async def admin_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.edit_start_text)
    await callback.message.answer("Отправьте новый текст для команды /start:")
    await callback.answer()


@router.message(AdminStates.edit_start_text)
async def admin_save_start(message: Message, state: FSMContext) -> None:
    cfg = load_config()
    cfg["start_text"] = message.text or ""
    save_config(cfg)
    await state.set_state(AdminStates.panel)
    await message.answer("Текст /start обновлён.", reply_markup=admin_panel_kb())


@router.callback_query(AdminStates.panel, F.data == "adm:add_req")
async def admin_add_req(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.add_required_channel)
    await callback.message.answer(
        "Отправьте канал для обязательной подписки: @username, ссылку https://t.me/..., "
        "или числовой ID (-100...). Бот должен иметь доступ к каналу для проверки подписки."
    )
    await callback.answer()


@router.message(AdminStates.add_required_channel)
async def admin_save_req(message: Message, state: FSMContext) -> None:
    ch = normalize_channel_input(message.text or "")
    if not ch:
        await message.answer("Не удалось распознать канал. Попробуйте снова.")
        return
    cfg = load_config()
    req = cfg.setdefault("required_channels", [])
    if any(str(x.get("chat_id")) == str(ch["chat_id"]) for x in req):
        await state.set_state(AdminStates.panel)
        await message.answer("Этот канал уже есть в списке.", reply_markup=admin_panel_kb())
        return
    req.append(ch)
    save_config(cfg)
    await state.set_state(AdminStates.panel)
    await message.answer(f"Канал добавлен: {ch['title']} ({ch['chat_id']})", reply_markup=admin_panel_kb())


@router.callback_query(AdminStates.panel, F.data == "adm:list_req")
async def admin_list_req(callback: CallbackQuery) -> None:
    cfg = load_config()
    req = cfg.get("required_channels") or []
    if not req:
        await callback.message.answer("Список обязательных каналов пуст.", reply_markup=admin_panel_kb())
    else:
        lines = ["Обязательные каналы:"]
        for i, ch in enumerate(req, 1):
            lines.append(f"{i}. {ch.get('title')} — {ch.get('chat_id')}")
        await callback.message.answer("\n".join(lines), reply_markup=admin_panel_kb())
    await callback.answer()


@router.callback_query(AdminStates.panel, F.data == "adm:del_req")
async def admin_del_req(callback: CallbackQuery, state: FSMContext) -> None:
    cfg = load_config()
    req = cfg.get("required_channels") or []
    if not req:
        await callback.message.answer("Список пуст.", reply_markup=admin_panel_kb())
        await callback.answer()
        return
    await state.set_state(AdminStates.remove_required_channel)
    lines = ["Отправьте номер канала для удаления:"]
    for i, ch in enumerate(req, 1):
        lines.append(f"{i}. {ch.get('title')} — {ch.get('chat_id')}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.message(AdminStates.remove_required_channel)
async def admin_do_del_req(message: Message, state: FSMContext) -> None:
    cfg = load_config()
    req = cfg.get("required_channels") or []
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("Нужно отправить номер из списка.")
        return
    idx = int(txt) - 1
    if idx < 0 or idx >= len(req):
        await message.answer("Неверный номер.")
        return
    removed = req.pop(idx)
    cfg["required_channels"] = req
    save_config(cfg)
    await state.set_state(AdminStates.panel)
    await message.answer(f"Удалён: {removed.get('title')}", reply_markup=admin_panel_kb())


@router.callback_query(AdminStates.panel, F.data == "adm:set_dest")
async def admin_set_dest(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.set_destination)
    await callback.message.answer(
        "Отправьте канал назначения (куда пускать после подписки): @username, "
        "ссылку https://t.me/... или 'clear' чтобы убрать."
    )
    await callback.answer()


@router.message(AdminStates.set_destination)
async def admin_save_dest(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    cfg = load_config()
    if txt.lower() == "clear":
        cfg["destination_channel"] = None
        save_config(cfg)
        await state.set_state(AdminStates.panel)
        await message.answer("Канал назначения убран.", reply_markup=admin_panel_kb())
        return
    ch = normalize_channel_input(txt)
    if not ch:
        await message.answer("Не удалось распознать канал. Попробуйте снова.")
        return
    cfg["destination_channel"] = ch
    save_config(cfg)
    await state.set_state(AdminStates.panel)
    await message.answer(
        f"Канал назначения установлен: {ch['title']} ({ch['chat_id']})",
        reply_markup=admin_panel_kb(),
    )


async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "Не задан токен бота. Установите переменную окружения TELEGRAM_BOT_TOKEN "
            "или укажите токен в BOT_TOKEN."
        )

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
