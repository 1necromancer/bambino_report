"""
OCR: этикетки (МАССА N) и весы (красные цифры).
Для весов — предобработка OpenCV: усиление красного и порог (блики дисплея).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np

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
    mass_value = None
    best_conf = 0.0

    # 1) Пытаемся найти паттерн "МАССА N ..." в одной строке
    mass_re = re.compile(
        r"масса\s*[:\s]*(\d+(?:[.,]\d+)?)\s*(?:кг|кгт|г|грамм|нетто)?", re.I
    )
    # 2) Если 'МАССА' и число разорваны (как в вашем примере),
    #    ищем просто число с единицами измерения.
    num_with_unit_re = re.compile(
        r"(\d+(?:[.,]\d+)?)\s*(?:кг|кгт|г|грамм|нетто)", re.I
    )

    for (_bbox, text, conf) in results:
        text_clean = text.replace("\n", " ")

        m = mass_re.search(text_clean)
        if not m:
            m = num_with_unit_re.search(text_clean)

        if m:
            try:
                num_str = m.group(1).replace(",", ".")
                value = float(num_str)
            except ValueError:
                continue

            # сохраняем вариант с наибольшей уверенностью
            if value is not None and conf >= best_conf:
                mass_value = value
                best_conf = conf

    return mass_value, float(best_conf) if best_conf else 0.0


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
