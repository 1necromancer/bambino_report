"""
«Склад» — быстрый просмотр остатков по каждому сорту.
Показывает текущий вес в граммах и, при наличии, примерные цены.
"""
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Price, Product

router = Router()


@router.callback_query(F.data == "stock_show")
async def stock_show(callback: CallbackQuery, session: AsyncSession) -> None:
    result = await session.execute(
        select(Product, Price)
        .join(Price, Product.id == Price.product_id, isouter=True)
        .order_by(Product.name, Price.size_grams)
    )
    rows = result.all()
    if not rows:
        await callback.message.edit_text(
            "На складе пока нет продуктов.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="« В меню", callback_data="menu")]]
            ),
        )
        await callback.answer()
        return

    # Group by product
    stock: dict[int, dict] = {}
    for product, price in rows:
        if product.id not in stock:
            stock[product.id] = {
                "name": product.name,
                "current_weight": product.current_weight_grams or Decimal("0"),
                "prices": [],
            }
        if price is not None:
            stock[product.id]["prices"].append(
                (price.size_grams, Decimal(str(price.sale_price)))
            )

    lines: list[str] = ["Склад сейчас:\n"]
    for item in stock.values():
        name = item["name"]
        weight = item["current_weight"]
        weight_str = f"{weight:.1f} г" if weight else "0 г"
        lines.append(f"• {name}: остаток {weight_str}")
        if item["prices"]:
            prices_str = ", ".join(
                f"{size} г — {price:.0f} ₸" for size, price in item["prices"]
            )
            lines.append(f"  Цены: {prices_str}")
        lines.append("")

    await callback.message.edit_text(
        "\n".join(lines).strip(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« В меню", callback_data="menu")]]
        ),
    )
    await callback.answer()

