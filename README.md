# Bambino — Telegram-бот учёта мороженого

Учёт прихода товара (OCR этикеток), ввод продаж, вечерняя инвентаризация (фото весов), автоматический расчёт штрафов и отчётность — всё через Telegram-бот на aiogram 3.

## Стек

- **Python 3.11**, aiogram 3, SQLAlchemy 2.0 (async + asyncpg)
- **OCR:** Google Cloud Vision API (основной) + EasyOCR (fallback) + OpenCV
- **БД:** PostgreSQL 16
- **Деплой:** Docker Compose

## Возможности

| Кнопка в боте | Что делает |
|---|---|
| **Приход товара** | Выбор сорта → фото этикетки → OCR массы партии (например «МАССА 4.122 КГ») → обновление остатка на складе |
| **Ввод продаж** | Выбор сорта → размер порции (300/500/700 г) → количество → запись в БД. Можно добавить несколько позиций подряд |
| **Инвентаризация (вечер)** | По каждому сорту с продажами за сегодня: фото весов → OCR веса → сравнение с ожидаемым остатком → штраф при недостаче |
| **Склад** | Текущие остатки (граммы) и цены по каждому сорту |

## Структура проекта

```
.
├── main.py                  # Точка входа: Bot, Dispatcher, polling
├── config.py                # TOKEN, DB_URL из .env
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
│
├── database/
│   ├── base.py              # DeclarativeBase (без циклических импортов)
│   ├── models.py            # Product, Price, SaleEntry, DailyReport
│   ├── session.py           # async engine, session factory, init_db()
│   └── __init__.py
│
├── handlers/
│   ├── menu.py              # /start, главное меню (inline-кнопки)
│   ├── receipt.py           # Приход: фото этикетки → GCV/EasyOCR → подтверждение
│   ├── sales.py             # Продажи: FSM (сорт → размер → кол-во)
│   ├── inventory.py         # Инвентаризация: фото весов → GCV/EasyOCR → отчёт
│   ├── report.py            # Сводка за день: выручка, разница, штрафы
│   ├── stock.py             # Склад: остатки и цены
│   ├── fallback.py          # Фото вне нужного состояния → подсказка
│   └── __init__.py
│
├── middlewares/
│   └── session.py           # DbSessionMiddleware: сессия БД в каждом handler
│
├── utils/
│   └── ocr.py               # OCR: GCV + EasyOCR + OpenCV-препроцессинг
│
└── scripts/
    ├── seed_products.py     # Первичное заполнение продуктов и цен
    └── debug_ocr.py         # CLI-отладка OCR на изображениях
```

## Логика работы

### Приход товара

1. Пользователь нажимает «Приход товара» → выбирает сорт.
2. Отправляет фото этикетки с текстом вроде `МАССА 4.122 КГ`.
3. Бот пробует **Google Cloud Vision** → если не получилось, **EasyOCR**.
4. Из найденных чисел выбирается масса поставки (приоритет: 1–100 кг).
5. Остаток сорта в БД увеличивается на распознанную массу.
6. При неудаче OCR — ручной ввод массы в граммах.

### Ввод продаж

1. «Ввод продаж» → выбор сорта → размер (300 / 500 / 700 г) → количество порций.
2. Запись `SaleEntry` в БД (product_id, size_grams, quantity, sale_price, report_date).
3. Кнопки «Добавить ещё» / «Завершить отчёт».

### Инвентаризация

1. «Инвентаризация (вечер)» — бот находит сорта с продажами за сегодня.
2. По каждому сорту рассчитывает **ожидаемый вес** = остаток в БД − проданные граммы.
3. Просит фото весов для каждого сорта.
4. OCR: **Google Cloud Vision** (кроп области «ВЕС кг», выбор числа ближайшего к ожидаемому) → fallback на **EasyOCR** (HSV-маска красного, `allowlist='0123456789.'`).
5. Чистый вес = распознанный вес − тара.
6. Разница = чистый вес − ожидаемый. При отрицательной разнице — **штраф** = |разница| × cost_per_gram.
7. Записывается `DailyReport`, обновляется текущий остаток.

### Склад

Показывает по каждому сорту: текущий остаток (г) и цены за размеры.

### Отчёт

Сводка за сегодня: выручка, разница (г), штраф — по каждому сорту и итого.

## База данных

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `products` | Сорта мороженого | name, cost_per_gram, tare_weight, current_weight_grams |
| `prices` | Цена за размер порции | product_id, size_grams (300/500/700), sale_price |
| `sale_entries` | Продажи за день | product_id, size_grams, quantity, sale_price, report_date |
| `daily_reports` | Сводка инвентаризации | product_id, report_date, expected_weight, actual_weight_from_photo, discrepancy_grams, total_revenue, penalty_amount |

**Формула инвентаризации:**
- Ожидаемый = `current_weight_grams` − сумма(size_grams × quantity) за день
- Чистый вес с фото = вес с фото − `tare_weight`
- Разница = чистый − ожидаемый
- Штраф = |разница| × `cost_per_gram` (при отрицательной разнице)

## OCR: два движка

| Движок | Для чего | Как работает |
|---|---|---|
| **Google Cloud Vision** | Этикетки (масса партии) и весы (основной) | `document_text_detection` на кропе; парсинг числа с единицами (КГ/г) |
| **EasyOCR** | Fallback для этикеток и весов | OpenCV-препроцессинг (HSV-маска красного, морфология), `allowlist='0123456789.'` для весов |

Vision вызывается первым; при ошибке (нет ключа, billing отключен, сеть) — автоматический fallback на EasyOCR.

## Запуск

### 1. Настройка окружения

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
TOKEN=<токен бота от @BotFather>
POSTGRES_USER=bombino
POSTGRES_PASSWORD=bombino_secret
POSTGRES_DB=bombino
```

### 2. Google Cloud Vision (опционально)

1. Создайте проект в [Google Cloud Console](https://console.cloud.google.com).
2. Включите **Vision API** (APIs & Services → Library → Vision API → Enable).
3. Привяжите **Billing** к проекту (Billing → Link a billing account).
4. Создайте **Service Account** (IAM & Admin → Service Accounts → Create).
   - Роль: `Cloud Vision API User`.
5. Создайте **JSON-ключ** (Keys → Add key → JSON) и сохраните как `gcp-vision-key.json` в корне проекта.

Без Vision бот работает на EasyOCR (медленнее и менее точно).

### 3. Docker Compose

```bash
docker compose up -d --build
```

### 4. Первичное заполнение продуктов

```bash
docker compose exec bot python -m scripts.seed_products
```

Добавляет три сорта (Пломбир, Ваниль, Шоколад) с ценами 700/1000/1500 ₸ за 90/150/210 г.

### 5. Использование

1. Откройте бота в Telegram по ссылке от @BotFather.
2. Отправьте `/start`.
3. Используйте кнопки меню.

## Локальный запуск (без Docker)

```bash
# Поднять PostgreSQL отдельно
cp .env.example .env
# Указать DB_URL для локального Postgres в .env

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m main

# В другом терминале:
python -m scripts.seed_products
```

## Отладка OCR

```bash
# Внутри Docker-контейнера:
docker compose exec bot python -m scripts.debug_ocr "path/to/image.jpg" label
docker compose exec bot python -m scripts.debug_ocr "path/to/image.jpg" scale
docker compose exec bot python -m scripts.debug_ocr "path/to/image.jpg" both
```

Выводит raw-результаты OCR и итоговое распознанное значение.

## Подключение к БД

```bash
# Через Docker:
docker compose exec db psql -U bombino -d bombino

# Или с хоста:
psql -h localhost -p 5432 -U bombino -d bombino
```

## Переменные окружения

| Переменная | Описание | Значение по умолчанию |
|---|---|---|
| `TOKEN` | Токен Telegram-бота | — (обязательна) |
| `DB_URL` | Строка подключения SQLAlchemy (async) | `postgresql+asyncpg://bombino:bombino_secret@localhost:5432/bombino` |
| `POSTGRES_USER` | Пользователь PostgreSQL | `bombino` |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL | `bombino_secret` |
| `POSTGRES_DB` | Имя базы данных | `bombino` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Путь к JSON-ключу Google Cloud (в контейнере) | `/app/gcp-vision-key.json` |
