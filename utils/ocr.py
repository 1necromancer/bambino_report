"""
OCR: этикетки (МАССА N) и весы (красные цифры).
Для весов — предобработка OpenCV: усиление красного и порог (блики дисплея).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple, List

import cv2
import numpy as np

try:
    from google.cloud import vision  # type: ignore[import]
    from google.api_core import exceptions as google_exceptions  # type: ignore[import]
except ImportError:  # pragma: no cover
    vision = None  # type: ignore[assignment]
    google_exceptions = None  # type: ignore[assignment]

# Lazy init reader to avoid loading at import
_reader = None
_reader_ru = None


def _get_reader(lang: tuple = ("ru", "en")):
    global _reader, _reader_ru
    if lang == ("ru", "en") and _reader is None:
        import easyocr
        _reader = easyocr.Reader(lang, gpu=False, verbose=False)
    if lang == ("ru", "en"):
        return _reader
    if _reader_ru is None:
        import easyocr
        _reader_ru = easyocr.Reader(("ru", "en"), gpu=False, verbose=False)
    return _reader_ru


def _resize_for_ocr(img: np.ndarray, max_side: int = 1300) -> np.ndarray:
    """
    Уменьшает очень большие снимки до разумного размера для OCR,
    сохраняя соотношение сторон. Это заметно ускоряет EasyOCR.
    """
    if img is None or img.size == 0:
        return img
    h, w = img.shape[:2]
    current_max = max(h, w)
    if current_max <= max_side:
        return img
    scale = max_side / float(current_max)
    new_w, new_h = int(w * scale), int(h * scale)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _hsv_red_mask(img: np.ndarray) -> np.ndarray:
    """HSV-маска для выделения красных пикселей (LED-сегменты)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([12, 255, 255]))
    mask2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([179, 255, 255]))
    return cv2.bitwise_or(mask1, mask2)


def _find_display_roi(img: np.ndarray) -> np.ndarray | None:
    """
    Автоматически находит область красного дисплея «ВЕС кг»:
    ищет крупнейший прямоугольный контур с красными пикселями
    в левой верхней части зоны дисплеев.
    Возвращает кроп или None.
    """
    mask = _hsv_red_mask(img)
    h, w = img.shape[:2]

    # Ищем контуры красных областей
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Фильтруем: только контуры в верхней левой половине и достаточного размера
    best_roi = None
    best_area = 0
    min_area = (h * w) * 0.002  # минимум 0.2% от площади

    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        area = cw * ch
        cx = x + cw / 2
        cy = y + ch / 2
        # Ожидаем дисплей «ВЕС» в левой половине, верхней 2/3
        if cx > w * 0.55 or cy > h * 0.7:
            continue
        if area < min_area:
            continue
        if area > best_area:
            best_area = area
            # Расширяем bbox чуть-чуть для запаса
            pad_x, pad_y = int(cw * 0.15), int(ch * 0.15)
            x1 = max(0, x - pad_x)
            y1 = max(0, y - pad_y)
            x2 = min(w, x + cw + pad_x)
            y2 = min(h, y + ch + pad_y)
            best_roi = img[y1:y2, x1:x2]

    return best_roi


def preprocess_scale_for_ocr(img: np.ndarray) -> np.ndarray:
    """
    Полный пайплайн подготовки изображения весов для EasyOCR:
    1) HSV-маска красного → убирает весь нецифровой шум
    2) Бинаризация (threshold)
    3) Дилатация (dilate) — «склеивает» сегменты в сплошные цифры
    4) Закрытие (close) — заполняет оставшиеся дырки
    """
    if img is None or img.size == 0:
        return img

    mask = _hsv_red_mask(img)

    # Бинаризация (маска уже 0/255, но на всякий случай)
    _, binary = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

    # Дилатация: делаем сегменты толще, чтобы EasyOCR видел цельные цифры
    dilate_kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(binary, dilate_kernel, iterations=2)

    # Закрытие: заполняем мелкие разрывы внутри цифр
    close_kernel = np.ones((5, 5), np.uint8)
    closed = cv2.morphologyEx(dilated, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    return closed


def extract_massa_from_label(image_path: str | Path) -> Tuple[float | None, float]:
    """
    Ищет на этикетке текст вида 'МАССА N' (или 'МАССА NЕТТО' и т.п.) и извлекает число N.
    Возвращает (значение в граммах или None, уверенность 0..1).
    """
    path = Path(image_path)
    if not path.exists():
        return None, 0.0
    img = cv2.imread(str(path))
    if img is None:
        return None, 0.0

    # Для этикетки берём нижнюю треть кадра по центру — там обычно находится стикер.
    h, w = img.shape[:2]
    cropped = img[int(h * 0.55) : int(h * 0.98), int(w * 0.15) : int(w * 0.85)]
    cropped = _resize_for_ocr(cropped)

    reader = _get_reader()
    results = reader.readtext(cropped)
    candidates: List[Tuple[float, float]] = []  # (grams, conf)

    # Число + кг → граммы; число + г → граммы
    mass_re_kg = re.compile(
        r"масса\s*[:\s]*(\d+(?:[.,]\d+)?)\s*(?:кг|кгт)?", re.I
    )
    num_kg_re = re.compile(
        r"(\d+(?:[.,]\d+)?)\s*[КK]г", re.I
    )
    num_g_re = re.compile(
        r"(\d+(?:[.,]\d+)?)\s*г(?:рамм)?", re.I
    )

    for (_bbox, text, conf) in results:
        text_clean = text.replace("\n", " ")

        for regex, to_grams in [(mass_re_kg, 1000.0), (num_kg_re, 1000.0), (num_g_re, 1.0)]:
            m = regex.search(text_clean)
            if m:
                try:
                    num_str = m.group(1).replace(",", ".")
                    value = float(num_str)
                    grams = value * to_grams
                    if 0 < grams < 1_000_000:
                        candidates.append((grams, conf))
                except ValueError:
                    pass
                break

    if not candidates:
        return None, 0.0

    # Приход = масса поставки (1–100 кг). Предпочитаем её.
    receipt_range = [(g, c) for g, c in candidates if 1000 <= g <= 100_000]
    if receipt_range:
        best = max(receipt_range, key=lambda x: x[1])
        return best[0], best[1]
    in_range = [(g, c) for g, c in candidates if 10 <= g <= 2000]
    if in_range:
        best = max(in_range, key=lambda x: x[1])
        return best[0], best[1]
    best = max(candidates, key=lambda x: x[1])
    return best[0], best[1]


def extract_weight_from_scale_image(
    image_path: str | Path,
    expected_grams: float | None = None,
) -> Tuple[float | None, float]:
    """
    Распознаёт вес на фото весов через EasyOCR с полным пайплайном:
    1) Кроп нижней части кадра (блок дисплеев).
    2) Автодетект ROI по красным контурам ИЛИ эмпирический кроп.
    3) HSV-маска → бинаризация → дилатация → сплошные цифры.
    4) EasyOCR с allowlist='0123456789.' и paragraph=False.
    5) Логический фильтр: если есть expected_grams — берём ±500 г.
    Возвращает (вес в граммах или None, уверенность).
    """
    path = Path(image_path)
    if not path.exists():
        return None, 0.0
    img = cv2.imread(str(path))
    if img is None:
        return None, 0.0

    h, w = img.shape[:2]
    display_block = img[int(h * 0.50) : int(h * 0.95), :]
    display_block = _resize_for_ocr(display_block, max_side=800)

    # Пробуем автоматически найти область дисплея по красным контурам
    roi = _find_display_roi(display_block)
    if roi is None or roi.size == 0:
        # Эмпирический кроп: левое верхнее окошко «ВЕС кг»
        dh, dw = display_block.shape[:2]
        roi = display_block[0 : int(dh * 0.55), 0 : int(dw * 0.45)]

    if roi is None or roi.size == 0:
        return None, 0.0

    preprocessed = preprocess_scale_for_ocr(roi)

    reader = _get_reader()
    results = reader.readtext(
        preprocessed,
        allowlist="0123456789.",
        paragraph=False,
    )

    candidates: List[Tuple[float, float]] = []  # (grams, conf)
    for (_bbox, text, conf) in results:
        text = text.strip()
        if not text:
            continue
        for m in re.finditer(r"(\d+(?:\.\d+)?)", text):
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            # Интерпретируем: если 0.001–50 → кг, если 100–50000 → граммы
            if 0.001 <= val <= 50.0:
                candidates.append((val * 1000, conf))  # кг → г
            if 100 <= val <= 50_000:
                candidates.append((val, conf))  # уже граммы

    if not candidates:
        return None, 0.0

    # Логический фильтр: если знаем ожидаемый вес, берём ±500 г
    if expected_grams is not None and expected_grams > 0:
        in_range = [
            (g, c) for g, c in candidates
            if abs(g - expected_grams) <= 500
        ]
        if in_range:
            best = min(in_range, key=lambda x: abs(x[0] - expected_grams))
            return best[0], best[1]
        # Ничего в ±500 г — берём ближайшее
        best = min(candidates, key=lambda x: abs(x[0] - expected_grams))
        return best[0], best[1]

    # Без ожидаемого — берём значение с максимальной уверенностью
    best = max(candidates, key=lambda x: x[1])
    return best[0], best[1]


def detect_text_gcv(image_path: str | Path) -> str:
    """
    Распознаёт текст на картинке с помощью Google Cloud Vision API.

    Возвращает полный текст (full_text_annotation) либо пустую строку,
    если Vision не настроен или произошла ошибка.
    """
    if vision is None:
        return ""

    path = Path(image_path)
    if not path.exists():
        return ""

    try:
        client = vision.ImageAnnotatorClient()
        with path.open("rb") as f:
            content = f.read()

        image = vision.Image(content=content)
        response = client.document_text_detection(image=image)
    except Exception as e:
        # 403 Billing disabled, network errors, etc. — не падаем, возвращаем пустую строку
        if google_exceptions and isinstance(e, google_exceptions.PermissionDenied):
            pass  # e.g. "billing must be enabled"
        return ""

    if response.error.message:
        return ""

    if response.full_text_annotation and response.full_text_annotation.text:
        return response.full_text_annotation.text

    texts: List[str] = [t.description for t in response.text_annotations]
    return "\n".join(texts)


def extract_massa_from_label_gcv(image_path: str | Path) -> Tuple[float | None, float]:
    """
    Распознаёт массу на этикетке через Google Cloud Vision.
    Ищет паттерны вида «МАССА 4.122 КГ», «0.092 КГ», «4,122 КГ» и т.п.
    Возвращает (масса в граммах или None, уверенность 0..1).
    """
    text = detect_text_gcv(image_path)
    if not text:
        return None, 0.0

    # Убираем типичные OCR-ошибки в цифрах (g→9, O→0)
    def fix_digit(s: str) -> str:
        s = s.replace("g", "9").replace("G", "9").replace("O", "0").replace("o", "0")
        return s

    # Любое число (с точкой/запятой) + кг/КГ или г (с опциональными буквами после)
    mass_re_kg = re.compile(
        r"(\d+(?:[.,]\d+)?)\s*[КK]г?\s*[А-ЯA-Z]*",
        re.I,
    )
    mass_re_g = re.compile(
        r"(\d+(?:[.,]\d+)?)\s*г(?:рамм)?",
        re.I,
    )

    candidates: List[Tuple[float, float]] = []  # (grams, conf)
    text_lower = text.lower()
    has_massa = "масса" in text_lower or "macca" in text_lower or "macса" in text_lower

    for line in text.splitlines():
        line = line.strip()
        for regex, to_grams in [(mass_re_kg, 1000.0), (mass_re_g, 1.0)]:
            for m in regex.finditer(line):
                try:
                    raw = m.group(1).replace(",", ".")
                    raw = fix_digit(raw)
                    num = float(raw)
                    grams = num * to_grams
                    if 0 < grams < 1_000_000:
                        conf = 0.92 if has_massa else 0.88
                        candidates.append((grams, conf))
                except ValueError:
                    continue

    # Ищем по всему тексту (на случай переносов)
    seen_grams: set[float] = {c[0] for c in candidates}
    full_block = " ".join(text.split())
    for regex, to_grams in [(mass_re_kg, 1000.0), (mass_re_g, 1.0)]:
        for m in regex.finditer(full_block):
            try:
                raw = m.group(1).replace(",", ".")
                raw = fix_digit(raw)
                num = float(raw)
                grams = num * to_grams
                if 0 < grams < 1_000_000 and grams not in seen_grams:
                    seen_grams.add(grams)
                    candidates.append((grams, 0.9))
            except ValueError:
                continue

    # Запасной вариант: в тексте есть "кг", ищем любое число 0.01–2.0 (вес продукта в кг)
    if not candidates and ("кг" in text_lower or "кг" in text):
        any_kg = re.findall(r"(\d+[.,]\d+)", text)
        for raw in any_kg:
            try:
                raw = fix_digit(raw.replace(",", "."))
                num = float(raw)
                if 0.01 <= num <= 2.0:
                    candidates.append((num * 1000, 0.75))
            except ValueError:
                continue

    if not candidates:
        return None, 0.0

    # Приход товара = масса поставки (партии), обычно 1–100 кг. Предпочитаем её.
    receipt_range = [(g, c) for g, c in candidates if 1000 <= g <= 100_000]
    if receipt_range:
        best = max(receipt_range, key=lambda x: x[1])
        return best[0], best[1]
    # Иначе — масса одной упаковки (10–2000 г)
    in_range = [(g, c) for g, c in candidates if 10 <= g <= 2000]
    if in_range:
        best = max(in_range, key=lambda x: x[1])
        return best[0], best[1]
    best = max(candidates, key=lambda x: x[1])
    return best[0], best[1]


def extract_weight_with_gcv(
    image_path: str | Path,
    expected_grams: float | None = None,
) -> Tuple[float | None, str]:
    """
    Распознаёт вес на весах через Google Cloud Vision.

    Кроп области «ВЕС кг», затем из всех чисел в 0.1–50 кг выбираем одно.
    Если передан expected_grams — берём значение, ближайшее к ожидаемому (в кг),
    чтобы отсечь 5.0 с клавиатуры при ожидаемом ~2 кг.
    Возвращает (вес_кг или None, полный_текст_с_кропа).
    """
    if vision is None:
        return None, ""

    path = Path(image_path)
    if not path.exists():
        return None, ""

    img = cv2.imread(str(path))
    if img is None:
        return None, ""
    h, w = img.shape[:2]

    # Кроп области «ВЕС кг»: левая часть кадра, дисплеи по вертикали
    # чуть шире/ниже, чтобы не обрезать цифры на разных ракурсах
    row_start = int(h * 0.50)
    row_end = int(h * 0.85)
    col_end = int(w * 0.45)
    weight_roi = img[row_start:row_end, 0:col_end]

    if weight_roi.size == 0:
        return None, ""

    _, buf = cv2.imencode(".jpg", weight_roi)
    content = buf.tobytes()

    try:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=content)
        response = client.document_text_detection(image=image)
    except Exception:
        return None, ""

    if response.error.message:
        return None, ""

    full_text = ""
    if response.full_text_annotation and response.full_text_annotation.text:
        full_text = response.full_text_annotation.text

    # Собираем все числа с кропа
    candidates: List[float] = []

    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                for word in para.words:
                    word_text = "".join(s.text for s in word.symbols)
                    for m in re.finditer(r"(\d+(?:[.,]\d+)?)", word_text):
                        try:
                            raw = m.group(1).replace(",", ".")
                            val = float(raw)
                            if 0.001 <= val <= 50.0:
                                candidates.append(val)
                            # Дисплей мог распознаться как "2020" без точки — тогда это граммы
                            if 100 <= val <= 50_000 and expected_grams is not None:
                                candidates.append(val / 1000.0)
                        except ValueError:
                            continue

    candidates = [c for c in candidates if c >= 0.1 and c <= 50.0]
    if not candidates:
        return None, full_text

    # Если знаем ожидаемый вес — берём кандидат, ближайший к нему (в кг)
    if expected_grams is not None and expected_grams > 0:
        expected_kg = expected_grams / 1000.0
        best = min(candidates, key=lambda c: abs(c - expected_kg))
        return best, full_text

    # Иначе — число с десятичной точкой (формат 2.020) или максимальное
    with_decimal = [c for c in candidates if c != int(c)]
    if with_decimal:
        return max(with_decimal), full_text
    return max(candidates), full_text
