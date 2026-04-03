"""
Debug OCR on real photos outside the bot.

Usage:
    python -m scripts.debug_ocr path/to/image.jpg [label|scale|both] [expected_grams]

Examples:
    python -m scripts.debug_ocr photo.jpg scale 2700
    python -m scripts.debug_ocr photo.jpg label
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from utils import ocr


def run_label_debug(path: Path) -> None:
    print(f"\n=== LABEL DEBUG for {path} ===")
    img = cv2.imread(str(path))
    if img is None:
        print("Could not read image.")
        return

    print("\n--- GCV (label) ---")
    gcv_value, gcv_conf = ocr.extract_massa_from_label_gcv(path)
    print(f"GCV parsed: value={gcv_value} g, conf={gcv_conf:.3f}")

    print("\n--- EasyOCR (label) ---")
    value, conf = ocr.extract_massa_from_label(path)
    print(f"EasyOCR parsed: value={value} g, conf={conf:.3f}")


def run_scale_debug(path: Path, expected_grams: float | None = None) -> None:
    print(f"\n=== SCALE DEBUG for {path} ===")
    if expected_grams:
        print(f"Expected weight: {expected_grams} g")

    img = cv2.imread(str(path))
    if img is None:
        print("Could not read image.")
        return

    h, w = img.shape[:2]
    display_block = img[int(h * 0.50) : int(h * 0.95), :]
    display_block = ocr._resize_for_ocr(display_block, max_side=800)

    print("\n--- Auto ROI detection ---")
    roi = ocr._find_display_roi(display_block)
    if roi is not None:
        print(f"Found ROI: {roi.shape[1]}x{roi.shape[0]} px")
    else:
        dh, dw = display_block.shape[:2]
        roi = display_block[0 : int(dh * 0.55), 0 : int(dw * 0.45)]
        print(f"No auto ROI, using empirical crop: {roi.shape[1]}x{roi.shape[0]} px")

    print("\n--- Preprocessed (HSV mask + dilate) ---")
    preprocessed = ocr.preprocess_scale_for_ocr(roi)
    red_pixels = np.count_nonzero(preprocessed)
    total_pixels = preprocessed.shape[0] * preprocessed.shape[1]
    print(f"Red pixels after mask: {red_pixels}/{total_pixels} ({100*red_pixels/total_pixels:.1f}%)")

    print("\n--- EasyOCR (preprocessed, allowlist=digits, paragraph=False) ---")
    reader = ocr._get_reader()
    results = reader.readtext(preprocessed, allowlist="0123456789.", paragraph=False)
    for _bbox, text, conf in results:
        print(f"  conf={conf:.3f}  text={text!r}")

    print("\n--- Full pipeline: extract_weight_from_scale_image ---")
    value, conf = ocr.extract_weight_from_scale_image(path, expected_grams)
    print(f"EasyOCR result: value={value} g, conf={conf:.3f}")

    print("\n--- Full pipeline: extract_weight_with_gcv ---")
    gcv_value, gcv_text = ocr.extract_weight_with_gcv(path, expected_grams)
    if gcv_value is not None:
        print(f"GCV result: value={gcv_value} kg ({gcv_value * 1000} g)")
    else:
        print("GCV result: None")
    if gcv_text:
        print(f"GCV raw text: {gcv_text[:300]!r}")


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        print("Usage: python -m scripts.debug_ocr path/to/image.jpg [label|scale|both] [expected_grams]")
        raise SystemExit(1)

    img_path = Path(argv[1]).expanduser().resolve()
    mode = argv[2] if len(argv) >= 3 else "both"
    expected_grams = float(argv[3]) if len(argv) >= 4 else None

    if not img_path.exists():
        print(f"Image not found: {img_path}")
        raise SystemExit(1)

    if mode in ("label", "both"):
        run_label_debug(img_path)
    if mode in ("scale", "both"):
        run_scale_debug(img_path, expected_grams)


if __name__ == "__main__":
    main(sys.argv)
