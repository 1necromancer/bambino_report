"""
Отчётность: сводная таблица — выручка, перерасход (г), итоговый штраф.
"""
from datetime import date
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DailyReport, Product

router = Router()


@router.callback_query(F.data == "report_show")
async def report_show(callback: CallbackQuery, session: AsyncSession) -> None:
    today = date.today()
    result = await session.execute(
        select(
            Product.name,
            DailyReport.total_revenue,
            DailyReport.discrepancy_grams,
            DailyReport.penalty_amount,
        )
        .join(Product, Product.id == DailyReport.product_id)
        .where(DailyReport.report_date == today)
    )
    rows = result.all()
    if not rows:
        await callback.message.edit_text("Нет отчёта за сегодня.")
        await callback.answer()
        return

    total_revenue = Decimal("0")
    total_penalty = Decimal("0")
    lines = ["Отчёт за сегодня:\n"]
    for name, revenue, disc, penalty in rows:
        total_revenue += revenue or 0
        total_penalty += penalty or 0
        disc_str = f"{disc:+.1f} г" if disc is not None else "—"
        lines.append(f"• {name}: выручка {revenue:.2f} ₽, разница {disc_str}, штраф {penalty:.2f} ₽")
    overuse = sum((r[2] or 0) for r in rows if (r[2] or 0) < 0)
    overuse_grams = abs(overuse) if overuse else 0
    lines.append("")
    lines.append(f"Итого выручка: {total_revenue:.2f} ₽")
    lines.append(f"Перерасход: {overuse_grams:.1f} г")
    lines.append(f"Итоговый штраф: {total_penalty:.2f} ₽")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="menu")],
        ]),
    )
    await callback.answer()
