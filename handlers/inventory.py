"""
Инвентаризация (вечер): после продаж — запрос фото весов по каждому сорту.
OCR красных цифр, сверка: остаток_БД - продажи_г = ожидаемый_вес;
чистый_вес_с_фото - ожидаемый = разница; при отрицательной разнице — штраф по cost_per_gram.
"""
import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import DailyReport, Product, SaleEntry
from utils.ocr import extract_weight_from_scale_image, extract_weight_with_gcv

router = Router()

CONFIDENCE_THRESHOLD = 0.5


# ── helpers ──────────────────────────────────────────────────────────


def _fmt_kg(grams: float) -> str:
    """2020.0 → '2.020 кг'"""
    return f"{grams / 1000:.3f} кг"


async def _save_inventory_result(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    actual_raw: float,
) -> None:
    """Общая логика сохранения результата инвентаризации (OCR или ручной ввод)."""
    data = await state.get_data()
    inventory_list = data["inventory_list"]
    idx = data["inventory_index"]
    product_id = data.get("inventory_current_product_id") or inventory_list[idx]["product_id"]
    item = next((x for x in inventory_list if x["product_id"] == product_id), None)
    if not item:
        await state.set_state("inventory_photo")
        await message.answer("Ошибка состояния. Начните инвентаризацию заново.")
        return

    product_name = item["product_name"]
    expected_weight = item["expected_weight"]
    total_revenue = Decimal(str(item["total_revenue"]))
    tare = item["tare_weight"]
    cost_per_gram = item["cost_per_gram"]

    actual_net = actual_raw - tare
    discrepancy = actual_net - expected_weight
    penalty = Decimal("0")
    if discrepancy < 0:
        penalty = Decimal(str(abs(discrepancy))) * Decimal(str(cost_per_gram))

    today = date.today()
    report = DailyReport(
        product_id=product_id,
        report_date=today,
        expected_weight=Decimal(str(expected_weight)),
        actual_weight_from_photo=Decimal(str(actual_net)),
        discrepancy_grams=Decimal(str(discrepancy)),
        total_revenue=total_revenue,
        penalty_amount=penalty,
    )
    session.add(report)
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one()
    product.current_weight_grams = Decimal(str(actual_net))
    await session.flush()

    await message.answer(
        f"«{product_name}»: ожидаемый {expected_weight:.0f} г, "
        f"с фото {actual_net:.1f} г, разница {discrepancy:.1f} г. "
        f"Штраф: {penalty:.2f} ₸."
    )

    await state.update_data(
        inventory_index=idx + 1,
        inventory_current_product_id=None,
        inventory_pending_raw=None,
    )
    await state.set_state("inventory_photo")
    await _ask_next_photo(message, session, state)


async def _ask_manual(message: Message, state: FSMContext, product_id: int, text: str) -> None:
    await state.set_state("inventory_manual_weight")
    await state.update_data(inventory_current_product_id=product_id)
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]),
    )


# ── flow ─────────────────────────────────────────────────────────────


@router.callback_query(F.data == "inventory_start")
async def inventory_start(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    today = date.today()
    subq = (
        select(SaleEntry.product_id)
        .where(SaleEntry.report_date == today)
        .distinct()
    )
    result = await session.execute(
        select(Product).where(Product.id.in_(subq)).order_by(Product.name)
    )
    products = list(result.scalars().all())
    if not products:
        await callback.message.edit_text(
            "Сегодня нет продаж для инвентаризации. Сначала введите продажи."
        )
        await callback.answer()
        return

    inventory_list = []
    for p in products:
        sold_result = await session.execute(
            select(
                func.coalesce(func.sum(SaleEntry.size_grams * SaleEntry.quantity), 0).label("sold_grams"),
                func.coalesce(func.sum(SaleEntry.quantity * SaleEntry.sale_price), 0).label("revenue"),
            ).where(SaleEntry.product_id == p.id, SaleEntry.report_date == today)
        )
        row = sold_result.one()
        sold_grams = row.sold_grams or 0
        revenue = row.revenue or 0
        expected = float(p.current_weight_grams or 0) - float(sold_grams)
        inventory_list.append({
            "product_id": p.id,
            "product_name": p.name,
            "tare_weight": float(p.tare_weight or 0),
            "cost_per_gram": float(p.cost_per_gram or 0),
            "expected_weight": expected,
            "total_revenue": float(revenue),
        })

    await state.update_data(inventory_list=inventory_list, inventory_index=0)
    await state.set_state("inventory_photo")
    await _ask_next_photo(callback.message, session, state)
    await callback.answer()


async def _ask_next_photo(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    inventory_list = data["inventory_list"]
    idx = data["inventory_index"]
    if idx >= len(inventory_list):
        await state.set_state("inventory_done")
        await message.answer(
            "Все фото получены. Формирую отчёт…",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Показать отчёт", callback_data="report_show")],
                [InlineKeyboardButton(text="« В меню", callback_data="menu")],
            ]),
        )
        return
    item = inventory_list[idx]
    name = item["product_name"]
    await message.answer(
        f"Фото весов для сорта «{name}» (красные цифры на дисплее):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]),
    )


@router.message(F.photo, StateFilter("inventory_photo"))
async def inventory_photo(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    inventory_list = data["inventory_list"]
    idx = data["inventory_index"]
    if idx >= len(inventory_list):
        return
    item = inventory_list[idx]
    product_id = item["product_id"]
    product_name = item["product_name"]
    expected_weight = item["expected_weight"]
    tare_weight = item["tare_weight"]

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    suffix = Path(file.file_path or "photo.jpg").suffix or ".jpg"
    path = Path("/tmp") / f"scale_{product_id}_{message.from_user.id}{suffix}"
    await message.bot.download_file(file.file_path, path)

    await message.answer("Обрабатываю фото весов…")
    actual_raw = None
    try:
        weight_kg, _ = await asyncio.to_thread(
            extract_weight_with_gcv, path, float(expected_weight)
        )
        if weight_kg is not None:
            actual_raw = weight_kg * 1000
        if actual_raw is None:
            actual_raw, _ = await asyncio.to_thread(
                extract_weight_from_scale_image, path, float(expected_weight)
            )
    except Exception:
        pass
    finally:
        path.unlink(missing_ok=True)

    if actual_raw is None:
        await _ask_manual(
            message, state, product_id,
            f"Не удалось распознать вес для «{product_name}». "
            "Введите вес вручную (число в граммах):",
        )
        return

    if actual_raw > 50_000:
        await _ask_manual(
            message, state, product_id,
            f"Распознано {actual_raw:.0f} г — похоже на ошибку. "
            "Введите вес вручную (граммы):",
        )
        return
    if actual_raw < 200 and expected_weight and expected_weight > 500:
        await _ask_manual(
            message, state, product_id,
            "Распознано слишком маленькое значение — "
            "введите вес вручную (граммы):",
        )
        return

    actual_net = actual_raw - tare_weight
    if actual_net < 0:
        await _ask_manual(
            message, state, product_id,
            "Распознанный вес меньше тары — невозможно. "
            "Введите вес вручную (граммы, брутто с тарой):",
        )
        return

    # ── Кнопка подтверждения ─────────────────────────────────────
    await state.update_data(
        inventory_current_product_id=product_id,
        inventory_pending_raw=actual_raw,
    )
    await state.set_state("inventory_confirm")

    tare_line = ""
    if tare_weight:
        tare_line = f"Тара: {tare_weight:.0f} г\nНетто: <b>{_fmt_kg(actual_net)}</b> ({actual_net:.0f} г)\n"

    await message.answer(
        f"«{product_name}»\n"
        f"Распознано с фото: <b>{_fmt_kg(actual_raw)}</b> ({actual_raw:.0f} г)\n"
        f"{tare_line}"
        f"Ожидаемый по базе: <b>{_fmt_kg(expected_weight)}</b> ({expected_weight:.0f} г)\n\n"
        "Это верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, верно", callback_data="inv_confirm_yes"),
                InlineKeyboardButton(text="Ввести вручную", callback_data="inv_confirm_manual"),
            ],
        ]),
    )


# ── Подтверждение / ручной ввод ──────────────────────────────────


@router.callback_query(F.data == "inv_confirm_yes", StateFilter("inventory_confirm"))
async def inventory_confirm_yes(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    actual_raw = data.get("inventory_pending_raw")
    if actual_raw is None:
        await callback.message.edit_text("Ошибка: нет сохранённого веса. Отправьте фото заново.")
        await state.set_state("inventory_photo")
        await callback.answer()
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await _save_inventory_result(callback.message, session, state, actual_raw)
    await callback.answer()


@router.callback_query(F.data == "inv_confirm_manual", StateFilter("inventory_confirm"))
async def inventory_confirm_manual(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state("inventory_manual_weight")
    await callback.message.answer("Введите вес вручную (число в граммах, брутто с тарой):")
    await callback.answer()


@router.message(F.text, StateFilter("inventory_manual_weight"))
async def inventory_manual_weight(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    try:
        actual_raw = float(message.text.replace(",", ".").strip())
    except ValueError:
        await message.answer("Введите число (граммы).")
        return

    await _save_inventory_result(message, session, state, actual_raw)
