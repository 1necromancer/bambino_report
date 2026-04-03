from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

router = Router()


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Приход товара", callback_data="receipt_start")],
        [InlineKeyboardButton(text="Ввод продаж", callback_data="sales_start")],
        [InlineKeyboardButton(text="Инвентаризация (вечер)", callback_data="inventory_start")],
        [InlineKeyboardButton(text="Склад", callback_data="stock_show")],
    ])


@router.message(F.text.in_({"/start", "/menu", "Меню", "меню"}))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Меню учёта мороженого:",
        reply_markup=main_kb(),
    )


@router.callback_query(F.data == "menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Меню учёта мороженого:", reply_markup=main_kb())
    await callback.answer()
