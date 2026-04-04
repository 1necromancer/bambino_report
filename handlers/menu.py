from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from config import OWNER_IDS, WEBAPP_URL

router = Router()


def main_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Приход товара", callback_data="receipt_start")],
        [InlineKeyboardButton(text="Ввод продаж", callback_data="sales_start")],
        [InlineKeyboardButton(text="Инвентаризация (вечер)", callback_data="inventory_start")],
        [InlineKeyboardButton(text="Склад", callback_data="stock_show")],
    ]
    if user_id in OWNER_IDS:
        rows.append([InlineKeyboardButton(text="⚙ Управление сортами", callback_data="admin_products")])
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="📱 Mini App", web_app=WebAppInfo(url=WEBAPP_URL))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.in_({"/start", "/menu", "Меню", "меню"}))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Меню учёта мороженого:",
        reply_markup=main_kb(message.from_user.id),
    )


@router.callback_query(F.data == "menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Меню учёта мороженого:",
        reply_markup=main_kb(callback.from_user.id),
    )
    await callback.answer()
