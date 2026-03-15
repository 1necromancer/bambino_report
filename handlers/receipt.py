"""
Приход товара: фото этикетки -> OCR «МАССА N» -> подтверждение -> обновление остатка в БД.
"""
import asyncio
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Product
from utils.ocr import extract_massa_from_label, extract_massa_from_label_gcv

router = Router()

CONFIDENCE_THRESHOLD = 0.5


def products_kb(products: list[Product], prefix: str = "receipt_product") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p.name, callback_data=f"{prefix}_{p.id}")]
        for p in products
    ] + [[InlineKeyboardButton(text="« В меню", callback_data="menu")]])


@router.callback_query(F.data == "receipt_start")
async def receipt_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    result = await session.execute(select(Product).order_by(Product.name))
    products = list(result.scalars().all())
    if not products:
        await callback.message.edit_text("Сначала добавьте продукты в БД (админ).")
        await callback.answer()
        return
    await callback.message.edit_text(
        "Приход товара. Выберите сорт:",
        reply_markup=products_kb(products),
    )
    await state.set_state("receipt_wait_product")
    await callback.answer()


@router.callback_query(F.data.startswith("receipt_product_"), F.data.regexp(r"receipt_product_(\d+)"))
async def receipt_product_chosen(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    product_id = int(callback.data.split("_")[-1])
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        await callback.answer("Продукт не найден.", show_alert=True)
        return
    await state.update_data(receipt_product_id=product_id, receipt_product_name=product.name)
    await state.set_state("receipt_wait_photo")
    await callback.message.edit_text(
        f"Сорт: {product.name}. Отправьте фото этикетки (с текстом МАССА N)."
    )
    await callback.answer()


@router.message(F.photo, lambda msg: msg.photo, StateFilter("receipt_wait_photo"))
async def receipt_photo(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    product_id = data["receipt_product_id"]
    product_name = data["receipt_product_name"]

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    suffix = Path(file.file_path or "photo.jpg").suffix or ".jpg"
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        await message.bot.download_file(file.file_path, tmp.name)
        path = Path(tmp.name)

    await message.answer("Обрабатываю фото…")
    mass_value = None
    confidence = 0.0
    try:
        mass_value, confidence = await asyncio.to_thread(
            extract_massa_from_label_gcv, path
        )
        if mass_value is None or confidence < CONFIDENCE_THRESHOLD:
            mass_value, confidence = await asyncio.to_thread(
                extract_massa_from_label, path
            )
    except Exception:
        pass
    finally:
        path.unlink(missing_ok=True)

    if mass_value is None or confidence < CONFIDENCE_THRESHOLD:
        await state.update_data(receipt_need_manual=True)
        await state.set_state("receipt_wait_manual")
        await message.answer(
            "Не удалось уверенно распознать массу. Введите массу вручную (число в граммах):"
        )
        return

    await _apply_receipt(session, product_id, Decimal(str(mass_value)))
    await state.clear()
    await message.answer(
        f"Приход по сорту «{product_name}»: +{mass_value} г. Остаток обновлён."
    )


@router.message(F.text, StateFilter("receipt_wait_manual"))
async def receipt_manual(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    try:
        mass_value = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer("Введите число (граммы), например 500 или 500.5")
        return
    if mass_value <= 0:
        await message.answer("Масса должна быть положительной.")
        return
    data = await state.get_data()
    product_id = data["receipt_product_id"]
    product_name = data["receipt_product_name"]
    await _apply_receipt(session, product_id, Decimal(str(mass_value)))
    await state.clear()
    await message.answer(f"Приход по сорту «{product_name}»: +{mass_value} г. Остаток обновлён.")


async def _apply_receipt(
    session: AsyncSession, product_id: int, mass_grams: Decimal
) -> None:
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one()
    product.current_weight_grams = (product.current_weight_grams or 0) + mass_grams
    await session.flush()
