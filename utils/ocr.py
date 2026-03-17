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


def preprocess_scale_display(img: np.ndarray) -> np.ndarray:
    """
    Предобработка изображения дисплея весов:
    - кроп области левого верхнего окошка «ВЕС кг»
    - маска по красному цвету в HSV
    - лёгкий морфологический фильтр
    """
    if img is None or img.size == 0:
        return img

    img = _resize_for_ocr(img, max_side=800)
    h, w = img.shape[:2]
    # Эмпирический кроп под ваши весы:
    # верхние 40% дисплея, левая треть — там окошко «ВЕС кг»
    top = int(h * 0.05)
    bottom = int(h * 0.55)
    left = int(w * 0.05)
    right = int(w * 0.45)
    crop = img[top:bottom, left:right]
    if crop.size == 0:
        crop = img

    # Переводим в HSV и выделяем красные пиксели
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Красный в HSV обычно попадает в два диапазона (через 180):
    lower_red1 = np.array([0, 70, 70])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 70, 70])
    upper_red2 = np.array([179, 255, 255])

    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)

    # Немного морфологии, чтобы цифры стали сплошнее
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    return mask


def _preprocess_scale_red(img: np.ndarray) -> np.ndarray:
    """
    Усиление красного и порог для дисплея весов (красные цифры, блики).
    Использует preprocess_scale_display для единого пайплайна.
    """
    return preprocess_scale_display(img)


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


def extract_weight_from_scale_image(image_path: str | Path) -> Tuple[float | None, float]:
    """
    Распознаёт вес (красные цифры) на фото весов после предобработки.
    Возвращает (вес в граммах или None, уверенность).
    """
    path = Path(image_path)
    if not path.exists():
        return None, 0.0
    img = cv2.imread(str(path))
    if img is None:
        return None, 0.0

    # Для весов оставляем нижнюю часть кадра, где расположен блок дисплеев и кнопки.
    h, w = img.shape[:2]
    display_block = img[int(h * 0.55) : int(h * 0.98), :]
    display_block = _resize_for_ocr(display_block, max_side=800)

    # Предобработка: выделяем только красный сегмент лев. верхнего окна «ВЕС кг»
    preprocessed = preprocess_scale_display(display_block)

    reader = _get_reader()
    # Ограничиваем алфавит только цифрами и точкой
    results = reader.readtext(preprocessed, allowlist="0123456789.")
    weight_value = None
    best_conf = 0.0
    # Число: целое или с точкой/запятой, возможно с 'г' или 'гр'
    num_re = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:г|гр|грамм)?")
    for (bbox, text, conf) in results:
        m = num_re.search(text.strip())
        if m:
            try:
                num_str = m.group(1).replace(",", ".")
                w = float(num_str)
                if 0 < w < 1_000_000:  # разумный диапазон для веса в граммах
                    weight_value = w
                    best_conf = max(best_conf, conf)
            except ValueError:
                continue
    return weight_value, float(best_conf) if best_conf else 0.0


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


def extract_weight_with_gcv(image_path: str | Path) -> Tuple[float | None, str]:
    """
    Использует Google Cloud Vision для распознавания веса на весах.

    Алгоритм:
      1. Кроп нижней части фото (где блок дисплеев и кнопки).
      2. Для каждого текстового блока смотрим его bounding box.
      3. Оставляем блоки в верхней левой части этого блока (там «ВЕС кг»).
      4. Из текста этих блоков извлекаем число (целое/с точкой).

    Возвращает (вес_кг или None, полный_распознанный_текст).
    """
    if vision is None:
        return None, ""

    path = Path(image_path)
    if not path.exists():
        return None, ""

    # Кропим нижнюю часть, где блок дисплеев и кнопки
    img = cv2.imread(str(path))
    if img is None:
        return None, ""
    h, w = img.shape[:2]
    display_block = img[int(h * 0.55) : int(h * 0.98), :]
    block_h, block_w = display_block.shape[:2]

    _, buf = cv2.imencode(".jpg", display_block)
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

    # Верхний левый квадрант блока дисплеев = окошко «ВЕС кг»
    candidates: List[Tuple[float, float]] = []  # (value_kg, score)

    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            xs = [v.x for v in block.bounding_box.vertices]
            ys = [v.y for v in block.bounding_box.vertices]
            if not xs or not ys:
                continue
            bx_min = min(xs)
            by_min = min(ys)

            # Координаты в системе display_block (0..block_w, 0..block_h)
            nx_min = bx_min / max(block_w, 1)
            ny_min = by_min / max(block_h, 1)

            if nx_min > 0.5 or ny_min > 0.5:
                continue

            # Собираем текст блока
            block_text_parts: List[str] = []
            for para in block.paragraphs:
                for word in para.words:
                    word_text = "".join([s.text for s in word.symbols])
                    block_text_parts.append(word_text)
            block_text = " ".join(block_text_parts)

            # Ищем число в этом тексте
            m = re.search(r"(\d+(?:[.,]\d+)?)", block_text)
            if not m:
                continue
            try:
                val = float(m.group(1).replace(",", "."))
            except ValueError:
                continue
            # Простой скор: чем левее/выше, тем лучше
            score = (1.0 - nx_min) + (1.0 - ny_min)
            candidates.append((val, score))

    if not candidates:
        return None, full_text

    # Берём самое «левое‑верхнее» число
    candidates.sort(key=lambda x: x[1], reverse=True)
    value_kg = candidates[0][0]
    return value_kg, full_text
