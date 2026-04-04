"""FastAPI application for Bambino Mini App."""
import hashlib
import hmac
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import OWNER_IDS, TOKEN
from database.models import DailyReport, Price, Product, SaleEntry
from database.session import async_session_maker


def _validate_init_data(init_data: str) -> dict | None:
    """Validate Telegram WebApp initData and return parsed user info."""
    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        check_hash = parsed.get("hash", [""])[0]
        if not check_hash:
            return None

        data_check_pairs = []
        for key, values in sorted(parsed.items()):
            if key == "hash":
                continue
            data_check_pairs.append(f"{key}={values[0]}")
        data_check_string = "\n".join(data_check_pairs)

        secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed, check_hash):
            return None

        import json
        user_data = parsed.get("user", ["{}"])[0]
        return json.loads(user_data)
    except Exception:
        return None


async def get_session():
    async with async_session_maker() as session:
        yield session
        await session.commit()


async def get_user(request: Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise HTTPException(401, "Missing Telegram init data")
    user = _validate_init_data(init_data)
    if not user:
        raise HTTPException(401, "Invalid Telegram init data")
    return user


def create_app() -> FastAPI:
    app = FastAPI(title="Bambino", docs_url=None, redoc_url=None)

    # ── Stock ────────────────────────────────────────────────────

    @app.get("/api/stock")
    async def api_stock(
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        result = await session.execute(
            select(Product).order_by(Product.name)
        )
        products = list(result.scalars().all())
        items = []
        for p in products:
            pr = await session.execute(
                select(Price).where(Price.product_id == p.id).order_by(Price.size_grams)
            )
            prices = [{"size": int(r.size_grams), "price": int(r.sale_price)} for r in pr.scalars().all()]
            items.append({
                "id": p.id,
                "name": p.name,
                "weight": float(p.current_weight_grams or 0),
                "tare": float(p.tare_weight or 0),
                "cost_per_gram": float(p.cost_per_gram or 0),
                "prices": prices,
            })
        return {"products": items, "is_owner": user.get("id") in OWNER_IDS}

    # ── Sales ────────────────────────────────────────────────────

    @app.get("/api/sales")
    async def api_sales(
        day: str | None = None,
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        target = date.fromisoformat(day) if day else date.today()
        result = await session.execute(
            select(SaleEntry, Product.name)
            .join(Product, SaleEntry.product_id == Product.id)
            .where(SaleEntry.report_date == target)
            .order_by(SaleEntry.created_at.desc())
        )
        rows = result.all()
        entries = []
        for sale, pname in rows:
            entries.append({
                "id": sale.id,
                "product": pname,
                "size": int(sale.size_grams),
                "qty": sale.quantity,
                "price": float(sale.sale_price),
                "total": float(sale.sale_price * sale.quantity),
            })
        total_revenue = sum(e["total"] for e in entries)
        return {"date": str(target), "entries": entries, "total_revenue": total_revenue}

    @app.post("/api/sales")
    async def api_add_sale(
        request: Request,
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        body = await request.json()
        product_id = body.get("product_id")
        size_grams = body.get("size_grams")
        quantity = body.get("quantity", 1)

        if not product_id or not size_grams or quantity <= 0:
            raise HTTPException(400, "Invalid data")

        price_row = await session.execute(
            select(Price).where(Price.product_id == product_id, Price.size_grams == size_grams)
        )
        price = price_row.scalar_one_or_none()
        if not price:
            raise HTTPException(404, "Price not found")

        entry = SaleEntry(
            product_id=product_id,
            size_grams=size_grams,
            quantity=quantity,
            sale_price=price.sale_price,
            report_date=date.today(),
        )
        session.add(entry)
        await session.flush()
        return {"ok": True, "total": float(price.sale_price * quantity)}

    @app.delete("/api/sales/{sale_id}")
    async def api_delete_sale(
        sale_id: int,
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        if user.get("id") not in OWNER_IDS:
            raise HTTPException(403, "Only owners can delete sales")
        result = await session.execute(select(SaleEntry).where(SaleEntry.id == sale_id))
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(404)
        await session.delete(entry)
        await session.flush()
        return {"ok": True}

    # ── Reports ──────────────────────────────────────────────────

    @app.get("/api/reports")
    async def api_reports(
        day: str | None = None,
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        target = date.fromisoformat(day) if day else date.today()
        result = await session.execute(
            select(DailyReport, Product.name)
            .join(Product, DailyReport.product_id == Product.id)
            .where(DailyReport.report_date == target)
            .order_by(Product.name)
        )
        rows = result.all()
        items = []
        total_revenue = Decimal("0")
        total_penalty = Decimal("0")
        for report, pname in rows:
            items.append({
                "product": pname,
                "expected": float(report.expected_weight),
                "actual": float(report.actual_weight_from_photo or 0),
                "discrepancy": float(report.discrepancy_grams or 0),
                "revenue": float(report.total_revenue),
                "penalty": float(report.penalty_amount),
            })
            total_revenue += report.total_revenue
            total_penalty += report.penalty_amount
        return {
            "date": str(target),
            "items": items,
            "total_revenue": float(total_revenue),
            "total_penalty": float(total_penalty),
        }

    # ── Admin: Products ──────────────────────────────────────────

    @app.post("/api/products")
    async def api_create_product(
        request: Request,
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        if user.get("id") not in OWNER_IDS:
            raise HTTPException(403)
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(400, "Name required")
        product = Product(
            name=name,
            tare_weight=Decimal(str(body.get("tare", 50))),
            cost_per_gram=Decimal(str(body.get("cost_per_gram", "0.06"))),
            current_weight_grams=Decimal("0"),
        )
        session.add(product)
        await session.flush()
        default_prices = [(90, 700), (150, 1000), (210, 1500)]
        for size, price in default_prices:
            session.add(Price(product_id=product.id, size_grams=size, sale_price=Decimal(str(price))))
        await session.flush()
        return {"ok": True, "id": product.id}

    @app.put("/api/products/{product_id}")
    async def api_update_product(
        product_id: int,
        request: Request,
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        if user.get("id") not in OWNER_IDS:
            raise HTTPException(403)
        body = await request.json()
        result = await session.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(404)
        if "name" in body:
            product.name = body["name"]
        if "tare" in body:
            product.tare_weight = Decimal(str(body["tare"]))
        if "cost_per_gram" in body:
            product.cost_per_gram = Decimal(str(body["cost_per_gram"]))
        await session.flush()
        return {"ok": True}

    @app.delete("/api/products/{product_id}")
    async def api_delete_product(
        product_id: int,
        session: AsyncSession = Depends(get_session),
        user: dict = Depends(get_user),
    ):
        if user.get("id") not in OWNER_IDS:
            raise HTTPException(403)
        await session.execute(delete(Price).where(Price.product_id == product_id))
        await session.execute(delete(Product).where(Product.id == product_id))
        await session.flush()
        return {"ok": True}

    # ── Static files ─────────────────────────────────────────────

    @app.get("/")
    async def index():
        return FileResponse("webapp/index.html")

    app.mount("/css", StaticFiles(directory="webapp/css"), name="css")
    app.mount("/js", StaticFiles(directory="webapp/js"), name="js")

    return app
