from aiogram import Router

from handlers import fallback, inventory, receipt, report, sales, stock
from handlers.menu import router as menu_router

router = Router(name="main")
router.include_router(menu_router)
router.include_router(receipt.router)
router.include_router(sales.router)
router.include_router(inventory.router)
router.include_router(report.router)
router.include_router(stock.router)
router.include_router(fallback.router)  # last: catch photo when not in a photo flow

__all__ = ["router"]
