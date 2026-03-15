"""
Small helper script to debug OCR on real photos outside the bot.

Usage (from project root):

    python -m scripts.debug_ocr path/to/image.jpg

It will:
  - run EasyOCR on the image (and the internal crops used by utils.ocr)
  - print all detected text fragments with confidence
  - run the same parsing logic as the bot and show what mass/weight was extracted
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

from utils import ocr


def run_label_debug(path: Path) -> None:
    print(f"\n=== LABEL DEBUG for {path} ===")
    img = cv2.imread(str(path))
    if img is None:
        print("Could not read image.")
        return
    h, w = img.shape[:2]
    cropped = img[int(h * 0.55) : int(h * 0.98), int(w * 0.15) : int(w * 0.85)]
    cropped = ocr._resize_for_ocr(cropped)  # type: ignore[attr-defined]

    reader = ocr._get_reader()  # type: ignore[attr-defined]
    results = reader.readtext(cropped)
    print("Raw EasyOCR results (label crop):")
    for bbox, text, conf in results:
        print(f"  conf={conf:.3f}  text={text!r}")

    value, conf = ocr.extract_massa_from_label(path)
    print(f"\nParsed mass from extract_massa_from_label: value={value}, conf={conf:.3f}")


def run_scale_debug(path: Path) -> None:
    print(f"\n=== SCALE DEBUG for {path} ===")
    img = cv2.imread(str(path))
    if img is None:
        print("Could not read image.")
        return
    h, w = img.shape[:2]
    display = img[int(h * 0.55) : int(h * 0.95), :]
    display = ocr._resize_for_ocr(display)  # type: ignore[attr-defined]

    reader = ocr._get_reader()  # type: ignore[attr-defined]
    results = reader.readtext(display)
    print("Raw EasyOCR results (scale display crop):")
    for bbox, text, conf in results:
        print(f"  conf={conf:.3f}  text={text!r}")

    value, conf = ocr.extract_weight_from_scale_image(path)
    print(f"\nParsed weight from extract_weight_from_scale_image: value={value}, conf={conf:.3f}")


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        print("Usage: python -m scripts.debug_ocr path/to/image.jpg [label|scale|both]")
        raise SystemExit(1)

    img_path = Path(argv[1]).expanduser().resolve()
    mode = argv[2] if len(argv) >= 3 else "both"

    if not img_path.exists():
        print(f"Image not found: {img_path}")
        raise SystemExit(1)

    if mode in ("label", "both"):
        run_label_debug(img_path)
    if mode in ("scale", "both"):
        run_scale_debug(img_path)


if __name__ == "__main__":
    main(sys.argv)

