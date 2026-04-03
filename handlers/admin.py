"""
Admin panel (owner only): manage products, prices.
Access controlled by OWNER_IDS from config.
"""
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config import OWNER_IDS
from database.models import Price, Product

router = Router()


def _is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def _back_admin() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="« Управление сортами", callback_data="admin_products")]]


def _back_menu() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="« В меню", callback_data="menu")]]


# ── Product list ─────────────────────────────────────────────────────


@router.callback_query(F.data == "admin_products")
async def admin_products(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    result = await session.execute(select(Product).order_by(Product.name))
    products = list(result.scalars().all())

    rows: list[list[InlineKeyboardButton]] = []
    for p in products:
        rows.append([InlineKeyboardButton(text=p.name, callback_data=f"admin_prod_{p.id}")])
    rows.append([InlineKeyboardButton(text="+ Добавить сорт", callback_data="admin_add_product")])
    rows.extend(_back_menu())

    text = "Управление сортами:" if products else "Нет сортов. Добавьте первый:"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


# ── Add product ──────────────────────────────────────────────────────


@router.callback_query(F.data == "admin_add_product")
async def admin_add_product(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state("admin_add_name")
    await callback.message.edit_text(
        "Введите название нового сорта:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_back_admin()),
    )
    await callback.answer()


@router.message(F.text, StateFilter("admin_add_name"))
async def admin_add_name(message: Message, state: FSMContext) -> None:
    if not _is_owner(message.from_user.id):
        return
    name = message.text.strip()
    if not name or name.startswith("/"):
        await message.answer("Введите корректное название.")
        return
    await state.update_data(admin_new_name=name)
    await state.set_state("admin_add_tare")
    await message.answer(
        f"Сорт: <b>{name}</b>\nВведите вес тары (граммы, например 50):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_back_admin()),
    )


@router.message(F.text, StateFilter("admin_add_tare"))
async def admin_add_tare(message: Message, state: FSMContext) -> None:
    if not _is_owner(message.from_user.id):
        return
    try:
        tare = Decimal(message.text.replace(",", ".").strip())
        if tare < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введите положительное число (граммы).")
        return
    await state.update_data(admin_new_tare=str(tare))
    await state.set_state("admin_add_cost")
    await message.answer(
        "Введите себестоимость за грамм (₸, например 0.06):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=_back_admin()),
    )


@router.message(F.text, StateFilter("admin_add_cost"))
async def admin_add_cost(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(message.from_user.id):
        return
    try:
        cost = Decimal(message.text.replace(",", ".").strip())
        if cost < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введите положительное число.")
        return

    data = await state.get_data()
    product = Product(
        name=data["admin_new_name"],
        tare_weight=Decimal(data["admin_new_tare"]),
        cost_per_gram=cost,
        current_weight_grams=Decimal("0"),
    )
    session.add(product)
    await session.flush()

    await state.clear()
    await state.update_data(admin_edit_product_id=product.id)
    await message.answer(
        f"Сорт «{product.name}» создан.\n\n"
        "Теперь добавьте хотя бы одну цену (порцию).\n"
        "Введите размер порции (граммы, например 90):",
    )
    await state.set_state("admin_price_size")


# ── View / edit product ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("admin_prod_"))
async def admin_view_product(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split("_")[-1])
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        await callback.answer("Сорт не найден.", show_alert=True)
        return

    prices_result = await session.execute(
        select(Price).where(Price.product_id == product_id).order_by(Price.size_grams)
    )
    prices = list(prices_result.scalars().all())

    lines = [
        f"<b>{product.name}</b>",
        f"Тара: {product.tare_weight} г",
        f"Себестоимость: {product.cost_per_gram} ₸/г",
        f"Остаток: {product.current_weight_grams} г",
        "",
    ]
    if prices:
        lines.append("Цены:")
        for p in prices:
            lines.append(f"  {int(p.size_grams)} г — {int(p.sale_price)} ₸")
    else:
        lines.append("Цены не заданы.")

    await state.update_data(admin_edit_product_id=product_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="+ Добавить цену", callback_data=f"admin_add_price_{product_id}")],
        [InlineKeyboardButton(text="Удалить цену", callback_data=f"admin_del_price_{product_id}")] if prices else [],
        [InlineKeyboardButton(text="Изменить тару", callback_data=f"admin_edit_tare_{product_id}")],
        [InlineKeyboardButton(text="Изменить себестоимость", callback_data=f"admin_edit_cost_{product_id}")],
        [InlineKeyboardButton(text="Удалить сорт", callback_data=f"admin_delete_{product_id}")],
        *_back_admin(),
    ])
    # Remove empty rows
    kb.inline_keyboard = [row for row in kb.inline_keyboard if row]

    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


# ── Add price to product ─────────────────────────────────────────────


@router.callback_query(F.data.startswith("admin_add_price_"))
async def admin_add_price_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split("_")[-1])
    await state.update_data(admin_edit_product_id=product_id)
    await state.set_state("admin_price_size")
    await callback.message.edit_text(
        "Введите размер порции (граммы, например 90):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад к сорту", callback_data=f"admin_prod_{product_id}")],
        ]),
    )
    await callback.answer()


@router.message(F.text, StateFilter("admin_price_size"))
async def admin_price_size(message: Message, state: FSMContext) -> None:
    if not _is_owner(message.from_user.id):
        return
    try:
        size = int(message.text.strip())
        if size <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое положительное число (граммы).")
        return
    await state.update_data(admin_price_size_val=size)
    await state.set_state("admin_price_amount")
    await message.answer(f"Порция: {size} г. Введите цену (₸):")


@router.message(F.text, StateFilter("admin_price_amount"))
async def admin_price_amount(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(message.from_user.id):
        return
    try:
        price = Decimal(message.text.replace(",", ".").strip())
        if price <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введите положительное число (₸).")
        return

    data = await state.get_data()
    product_id = data["admin_edit_product_id"]
    size = data["admin_price_size_val"]

    existing = await session.execute(
        select(Price).where(Price.product_id == product_id, Price.size_grams == size)
    )
    row = existing.scalar_one_or_none()
    if row:
        row.sale_price = price
    else:
        session.add(Price(product_id=product_id, size_grams=size, sale_price=price))
    await session.flush()

    await state.set_state(None)
    await message.answer(
        f"Цена {size} г — {int(price)} ₸ сохранена.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="+ Ещё цену", callback_data=f"admin_add_price_{product_id}")],
            [InlineKeyboardButton(text="« К сорту", callback_data=f"admin_prod_{product_id}")],
            *_back_admin(),
        ]),
    )


# ── Delete price ─────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("admin_del_price_"))
async def admin_del_price_list(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split("_")[-1])
    prices_result = await session.execute(
        select(Price).where(Price.product_id == product_id).order_by(Price.size_grams)
    )
    prices = list(prices_result.scalars().all())
    if not prices:
        await callback.answer("Нет цен для удаления.", show_alert=True)
        return

    rows = [
        [InlineKeyboardButton(
            text=f"❌ {int(p.size_grams)} г — {int(p.sale_price)} ₸",
            callback_data=f"admin_rm_price_{p.id}",
        )]
        for p in prices
    ]
    rows.append([InlineKeyboardButton(text="« Назад к сорту", callback_data=f"admin_prod_{product_id}")])

    await callback.message.edit_text(
        "Выберите цену для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_rm_price_"))
async def admin_rm_price(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    price_id = int(callback.data.split("_")[-1])
    result = await session.execute(select(Price).where(Price.id == price_id))
    price = result.scalar_one_or_none()
    if not price:
        await callback.answer("Цена не найдена.", show_alert=True)
        return
    product_id = price.product_id
    await session.delete(price)
    await session.flush()
    await callback.answer(f"Удалена цена {int(price.size_grams)} г.")

    # Refresh the product view
    callback.data = f"admin_prod_{product_id}"
    await admin_view_product(callback, session, state)


# ── Edit tare ────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("admin_edit_tare_"))
async def admin_edit_tare_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split("_")[-1])
    await state.update_data(admin_edit_product_id=product_id)
    await state.set_state("admin_set_tare")
    await callback.message.edit_text(
        "Введите новый вес тары (граммы):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад к сорту", callback_data=f"admin_prod_{product_id}")],
        ]),
    )
    await callback.answer()


@router.message(F.text, StateFilter("admin_set_tare"))
async def admin_set_tare(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(message.from_user.id):
        return
    try:
        tare = Decimal(message.text.replace(",", ".").strip())
        if tare < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введите положительное число (граммы).")
        return
    data = await state.get_data()
    product_id = data["admin_edit_product_id"]
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one()
    product.tare_weight = tare
    await session.flush()
    await state.set_state(None)
    await message.answer(
        f"Тара для «{product.name}» обновлена: {tare} г.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К сорту", callback_data=f"admin_prod_{product_id}")],
            *_back_admin(),
        ]),
    )


# ── Edit cost_per_gram ───────────────────────────────────────────────


@router.callback_query(F.data.startswith("admin_edit_cost_"))
async def admin_edit_cost_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split("_")[-1])
    await state.update_data(admin_edit_product_id=product_id)
    await state.set_state("admin_set_cost")
    await callback.message.edit_text(
        "Введите новую себестоимость за грамм (₸):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад к сорту", callback_data=f"admin_prod_{product_id}")],
        ]),
    )
    await callback.answer()


@router.message(F.text, StateFilter("admin_set_cost"))
async def admin_set_cost(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(message.from_user.id):
        return
    try:
        cost = Decimal(message.text.replace(",", ".").strip())
        if cost < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введите положительное число.")
        return
    data = await state.get_data()
    product_id = data["admin_edit_product_id"]
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one()
    product.cost_per_gram = cost
    await session.flush()
    await state.set_state(None)
    await message.answer(
        f"Себестоимость для «{product.name}» обновлена: {cost} ₸/г.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К сорту", callback_data=f"admin_prod_{product_id}")],
            *_back_admin(),
        ]),
    )


# ── Delete product ───────────────────────────────────────────────────


@router.callback_query(F.data.startswith("admin_delete_"))
async def admin_delete_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split("_")[-1])
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        await callback.answer("Сорт не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        f"Удалить сорт «{product.name}» со всеми ценами и историей? Это необратимо.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"admin_confirm_del_{product_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"admin_prod_{product_id}"),
            ],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_confirm_del_"))
async def admin_confirm_delete(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    product_id = int(callback.data.split("_")[-1])
    result = await session.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        await callback.answer("Сорт не найден.", show_alert=True)
        return

    name = product.name
    await session.execute(delete(Price).where(Price.product_id == product_id))
    await session.delete(product)
    await session.flush()

    await callback.message.edit_text(f"Сорт «{name}» удалён.")
    await callback.answer()

    callback.data = "admin_products"
    await admin_products(callback, session, state)
