# Bombino — бот учёта мороженого (aiogram 3)

Учёт прихода по этикеткам (OCR «МАССА N»), ввод продаж (сорт → вес → кол-во), вечерняя инвентаризация по фото весов и отчётность.

## Стек

- Python 3.11+, aiogram 3, SQLAlchemy 2.0 (async + asyncpg), EasyOCR, OpenCV
- PostgreSQL, Docker Compose

## Структура проекта

```
.
├── main.py                 # Точка входа бота
├── config.py               # TOKEN, DB_URL из .env
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── database/
│   ├── models.py           # Product, Price, SaleEntry, DailyReport
│   └── session.py          # async engine, session factory, init_db
├── handlers/
│   ├── menu.py             # /start, главное меню
│   ├── receipt.py          # Приход: фото этикетки → OCR → подтверждение
│   ├── sales.py            # Продажи: FSM (сорт → вес → кол-во), «Добавить еще» / «Завершить»
│   ├── inventory.py        # Инвентаризация: фото весов по сортам, сверка, штраф
│   └── report.py           # Сводка: выручка, перерасход (г), итоговый штраф
├── middlewares/
│   └── session.py          # DbSessionMiddleware
├── utils/
│   └── ocr.py              # Этикетки (МАССА N), весы (красные цифры + OpenCV)
└── scripts/
    └── seed_products.py    # Первичное заполнение продуктов и цен
```

## Запуск

1. Скопировать `.env.example` в `.env`, указать `TOKEN` и при необходимости `DB_URL` / учётные данные PostgreSQL.
2. Поднять сервисы:
   ```bash
   docker compose up -d
   ```
3. (Опционально) Заполнить продукты и цены:
   ```bash
   docker compose exec bot python -m scripts.seed_products
   ```

Локально (без Docker):

```bash
cp .env.example .env
# Отредактировать .env, поднять PostgreSQL
pip install -r requirements.txt
python -m main
# В другом терминале: python -m scripts.seed_products
```

## Логика БД

- **products**: название, cost_per_gram, tare_weight, current_weight_grams (остаток).
- **prices**: product_id, size_grams (300/500/700), sale_price.
- **sale_entries**: продажи за день (product_id, size_grams, quantity, sale_price, report_date).
- **daily_reports**: по каждому продукту за день — expected_weight, actual_weight_from_photo, discrepancy_grams, total_revenue, penalty_amount.

Инвентаризация: ожидаемый = остаток_БД − (продажи в граммах); чистый вес с фото = вес с фото − tare_weight; разница = чистый − ожидаемый; при отрицательной разнице штраф = |разница| × cost_per_gram.
