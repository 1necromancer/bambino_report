"""
Скрипт первичного заполнения: продукты и цены (300/500/700 г).
Запуск: python -m scripts.seed_products (после поднятия БД).
"""
import asyncio
from decimal import Decimal

from sqlalchemy import select

from config import DB_URL  # noqa: F401 - load .env
from database.models import Price, Product
from database.session import async_session_maker, init_db


async def main() -> None:
    await init_db()
    async with async_session_maker() as session:
        result = await session.execute(select(Product))
        if result.scalars().first() is not None:
            print("Продукты уже есть, пропуск.")
            return
        products_data = [
            ("Пломбир", Decimal("0.05"), Decimal("50")),
            ("Ваниль", Decimal("0.04"), Decimal("50")),
            ("Шоколад", Decimal("0.06"), Decimal("50")),
        ]
        sizes_prices = [
            (90, 700),
            (150, 1000),
            (210, 1500),
        ]
        for name, cost_per_gram, tare in products_data:
            p = Product(
                name=name,
                cost_per_gram=cost_per_gram,
                tare_weight=tare,
                current_weight_grams=0,
            )
            session.add(p)
            await session.flush()
            for size, price in sizes_prices:
                session.add(Price(product_id=p.id, size_grams=size, sale_price=Decimal(str(price))))
        await session.commit()
        print("Добавлены продукты и цены.")


if __name__ == "__main__":
    asyncio.run(main())
