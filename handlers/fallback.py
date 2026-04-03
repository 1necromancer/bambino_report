"""Fallback handlers when user sends something the bot doesn't expect in current state."""
from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

router = Router()


@router.message(F.photo)
async def photo_without_flow(message: Message) -> None:
    await message.answer(
        "Фото можно отправить только после выбора действия:\n\n"
        "• <b>Приход товара</b> — нажмите «Приход товара», выберите сорт, затем отправьте фото этикетки.\n"
        "• <b>Инвентаризация</b> — нажмите «Инвентаризация (вечер)», затем по очереди отправляйте фото весов по каждому сорту.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]),
    )
