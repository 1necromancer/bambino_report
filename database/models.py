from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base

if TYPE_CHECKING:
    from sqlalchemy.orm import relationship as rel


class Product(Base):
    """Сорт мороженого. current_weight_grams — текущий остаток на складе."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cost_per_gram: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    tare_weight: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    current_weight_grams: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    prices: Mapped[list["Price"]] = relationship("Price", back_populates="product")
    sale_entries: Mapped[list["SaleEntry"]] = relationship("SaleEntry", back_populates="product")
    daily_reports: Mapped[list["DailyReport"]] = relationship("DailyReport", back_populates="product")


class Price(Base):
    """Цена за размер (300, 500, 700 г)."""

    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    size_grams: Mapped[int] = mapped_column(nullable=False)  # 300, 500, 700
    sale_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    product: Mapped["Product"] = relationship("Product", back_populates="prices")


class SaleEntry(Base):
    """Одна запись о продаже (сорт + размер + кол-во) за день. Для расчёта выручки и ожидаемого остатка."""

    __tablename__ = "sale_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    size_grams: Mapped[int] = mapped_column(nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    report_date: Mapped[date] = mapped_column(Date(), nullable=False, server_default=func.current_date())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    product: Mapped["Product"] = relationship("Product", back_populates="sale_entries")


class DailyReport(Base):
    """Сводка по продукту за день: ожидаемый/фактический вес, разница, выручка, штраф."""

    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    report_date: Mapped[date] = mapped_column(Date(), nullable=False)
    expected_weight: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    actual_weight_from_photo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    discrepancy_grams: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    penalty_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    product: Mapped["Product"] = relationship("Product", back_populates="daily_reports")
