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


def preprocess_scale_display(img: np.ndarray) -> np.ndarray:
    """
    Предобработка изображения дисплея весов для лучшего распознавания
    красных сегментных цифр (LED/LCD). Уменьшает блики, выделяет красный,
    усиливает контраст и даёт бинарное изображение (белые цифры на чёрном).
    """
    if img is None or img.size == 0:
        return img
    # 1. Сглаживание для уменьшения бликов (сохраняет границы лучше, чем Gaussian)
    denoised = cv2.bilateralFilter(img, d=9, sigmaColor=75, sigmaSpace=75)
    if len(denoised.shape) == 2:
        red_channel = denoised
    else:
        b, g, r = cv2.split(denoised)
        # 2. Выделение красного: красный минус зелёный и синий (сегменты обычно чисто красные)
        red_enhanced = cv2.subtract(r, cv2.addWeighted(g, 0.5, b, 0.5, 0))
        red_channel = np.clip(red_enhanced, 0, 255).astype(np.uint8)
    # 3. Повышение контраста (сегменты ярче фона)
    red_channel = cv2.normalize(red_channel, None, 0, 255, cv2.NORM_MINMAX)
    # 4. Адаптивный порог — устойчивее к неравномерной подсветке и бликам
    thresh = cv2.adaptiveThreshold(
        red_channel, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    # 5. Морфология: закрытие — склеивает разрывы в сегментах цифр
    kernel_close = np.ones((2, 2), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_close)
    # 6. Лёгкое открытие — убирает мелкий шум, не разъедая тонкие сегменты
    kernel_open = np.ones((1, 1), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_open)
    return thresh


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
    reader = _get_reader()
    results = reader.readtext(img)
    mass_value = None
    best_conf = 0.0
    # Паттерн: МАССА и рядом число (целое или с запятой/точкой)
    mass_re = re.compile(r"масса\s*[:\s]*(\d+(?:[.,]\d+)?)\s*(?:г|грамм|нетто)?", re.I)
    for (bbox, text, conf) in results:
        text_clean = text.replace(" ", "").replace("\n", " ")
        m = mass_re.search(text_clean) or mass_re.search(text)
        if m:
            try:
                num_str = m.group(1).replace(",", ".")
                mass_value = float(num_str)
                best_conf = max(best_conf, conf)
            except ValueError:
                continue
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
    preprocessed = _preprocess_scale_red(img)
    reader = _get_reader()
    results = reader.readtext(preprocessed)
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
