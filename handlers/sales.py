"""
Ввод продаж: FSM — выбор сорта (inline) -> выбор веса (300/500/700) -> количество.
Кнопки «Добавить еще» и «Завершить отчёт».
"""
from datetime import date
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Price, Product, SaleEntry

router = Router()


def products_kb(products: list[Product]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p.name, callback_data=f"sales_product_{p.id}")]
        for p in products
    ] + [[InlineKeyboardButton(text="« В меню", callback_data="menu")]])


def sizes_kb(prices: list[Price]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{int(p.size_grams)} г — {int(p.sale_price)} ₸",
            callback_data=f"sales_size_{p.product_id}_{int(p.size_grams)}",
        )
        for p in sorted(prices, key=lambda x: x.size_grams)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [b] for b in buttons
    ] + [[InlineKeyboardButton(text="« В меню", callback_data="menu")]])


def add_or_finish_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Добавить еще", callback_data="sales_start"),
            InlineKeyboardButton(text="Завершить отчёт", callback_data="sales_finish"),
        ],
        [InlineKeyboardButton(text="« В меню", callback_data="menu")],
    ])


@router.callback_query(F.data == "sales_start")
async def sales_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    result = await session.execute(
        select(Product).where(Product.id.in_(select(Price.product_id).distinct())).order_by(Product.name)
    )
    products = list(result.scalars().all())
    if not products:
        await callback.message.edit_text("Нет продуктов с ценами. Добавьте цены в БД.")
        await callback.answer()
        return
    await callback.message.edit_text(
        "Ввод продаж. Выберите сорт:",
        reply_markup=products_kb(products),
    )
    await state.set_state("sales_choose_product")
    await callback.answer()


@router.callback_query(F.data.regexp(r"sales_product_(\d+)"), StateFilter("sales_choose_product"))
async def sales_product_chosen(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    product_id = int(callback.data.split("_")[-1])
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        await callback.answer("Продукт не найден.", show_alert=True)
        return
    prices_result = await session.execute(
        select(Price).where(Price.product_id == product_id).order_by(Price.size_grams)
    )
    prices = list(prices_result.scalars().all())
    if not prices:
        await callback.answer("Нет цен для этого сорта.", show_alert=True)
        return
    await state.update_data(sales_product_id=product_id, sales_product_name=product.name)
    await state.set_state("sales_choose_size")
    await callback.message.edit_text(
        f"Сорт: {product.name}. Выберите порцию:",
        reply_markup=sizes_kb(prices),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"sales_size_(\d+)_(\d+)"), StateFilter("sales_choose_size"))
async def sales_size_chosen(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    parts = callback.data.split("_")
    product_id = int(parts[2])
    size_grams = int(parts[3])
    result = await session.execute(
        select(Price).where(Price.product_id == product_id, Price.size_grams == size_grams)
    )
    price_row = result.scalar_one_or_none()
    if not price_row:
        await callback.answer("Цена для этого размера не задана.", show_alert=True)
        return
    await state.update_data(
        sales_product_id=product_id,
        sales_size_grams=size_grams,
        sales_sale_price=float(price_row.sale_price),
    )
    await state.set_state("sales_enter_quantity")
    await callback.message.edit_text("Введите количество проданных порций (целое число):")
    await callback.answer()


@router.message(F.text, StateFilter("sales_enter_quantity"))
async def sales_quantity(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    try:
        qty = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число (количество порций).")
        return
    if qty <= 0:
        await message.answer("Количество должно быть больше 0.")
        return
    data = await state.get_data()
    product_id = data["sales_product_id"]
    product_name = data["sales_product_name"]
    size_grams = data["sales_size_grams"]
    sale_price = Decimal(str(data["sales_sale_price"]))

    today = date.today()
    entry = SaleEntry(
        product_id=product_id,
        size_grams=size_grams,
        quantity=qty,
        sale_price=sale_price,
        report_date=today,
    )
    session.add(entry)
    await session.flush()

    revenue = sale_price * qty
    grams = size_grams * qty
    await state.clear()
    await message.answer(
        f"Добавлено: {product_name}, {size_grams} г × {qty} = {grams} г, выручка {revenue} ₸.\n\nДобавить ещё или завершить отчёт?",
        reply_markup=add_or_finish_kb(),
    )


@router.callback_query(F.data == "sales_finish")
async def sales_finish(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Отчёт по продажам завершён. Переходите к инвентаризации (вечер): сделайте фото весов по каждому сорту.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Начать инвентаризацию", callback_data="inventory_start")],
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]),
    )
    await callback.answer()
