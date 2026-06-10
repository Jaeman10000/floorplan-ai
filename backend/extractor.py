# extractor.py
# 도면 이미지 → 외곽 좌표 자동 추출
# 파이프라인: 전처리 → 외곽선 검출 → Douglas-Peucker 단순화 → OCR 스케일 보정

import cv2
import numpy as np
import math
from typing import List, Tuple, Optional
from dataclasses import dataclass

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


@dataclass
class ExtractionResult:
    pts_px: List[Tuple[int, int]]
    pts_mm: List[Tuple[float, float]]
    scale_mm_per_px: float
    area_m2: float
    confidence: float
    ocr_dimensions: List[float]
    warnings: List[str]


def extract_outline(
    image_path: str,
    known_area_m2: Optional[float] = None,
    scale_hint_mm_per_px: Optional[float] = None,
    epsilon_ratio: float = 0.01,
    min_area_ratio: float = 0.05,
) -> ExtractionResult:
    warnings = []
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"이미지를 읽을 수 없습니다: {image_path}")
    h, w = img.shape[:2]
    img_area = h * w
    processed, thresh = _preprocess(img)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("외곽선을 찾을 수 없습니다.")
    min_area = img_area * min_area_ratio
    valid_contours = [c for c in contours if cv2.contourArea(c) > min_area]
    if not valid_contours:
        valid_contours = sorted(contours, key=cv2.contourArea, reverse=True)[:1]
        warnings.append("외곽 후보 부족 — 가장 큰 윤곽 사용.")
    main_contour = max(valid_contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(main_contour, True)
    epsilon = epsilon_ratio * perimeter
    approx = cv2.approxPolyDP(main_contour, epsilon, True)
    if len(approx) > 20:
        epsilon = 0.02 * perimeter
        approx = cv2.approxPolyDP(main_contour, epsilon, True)
        warnings.append(f"꼭짓점 과다 — 단순화 강도 높임 ({len(approx)}개)")
    pts_px = [(int(p[0][0]), int(p[0][1])) for p in approx]
    scale, ocr_dims = _determine_scale(img, pts_px, known_area_m2, scale_hint_mm_per_px, warnings)
    pts_mm = [(x * scale, y * scale) for x, y in pts_px]
    area_m2 = _shoelace_area_m2(pts_mm)
    if known_area_m2 and abs(area_m2 - known_area_m2) > known_area_m2 * 0.1:
        warnings.append(f"계산면적({area_m2:.1f}m²) vs 입력면적({known_area_m2}m²) 차이 큼.")
    confidence = _calc_confidence(pts_px, ocr_dims, known_area_m2, area_m2, warnings)
    return ExtractionResult(
        pts_px=pts_px, pts_mm=pts_mm, scale_mm_per_px=scale,
        area_m2=round(area_m2, 2), confidence=confidence,
        ocr_dimensions=ocr_dims, warnings=warnings,
    )


def _preprocess(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)
    return gray, opened


def _determine_scale(img, pts_px, known_area_m2, scale_hint, warnings):
    ocr_dims = []
    if OCR_AVAILABLE:
        ocr_dims = _ocr_dimensions(img)
        if ocr_dims:
            xs = [p[0] for p in pts_px]; ys = [p[1] for p in pts_px]
            max_bbox_px = max(max(xs)-min(xs), max(ys)-min(ys))
            if max_bbox_px > 0:
                return max(ocr_dims) / max_bbox_px, ocr_dims
    if known_area_m2:
        poly_area_px2 = _shoelace_area_px2(pts_px)
        if poly_area_px2 > 0:
            return math.sqrt(known_area_m2 * 1e6 / poly_area_px2), ocr_dims
    if scale_hint:
        return scale_hint, ocr_dims
    warnings.append("스케일 자동 결정 실패. 이미지 너비=10,000mm 가정.")
    h, w = img.shape[:2]
    return 10000.0 / w, ocr_dims


def _ocr_dimensions(img):
    import re
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    pil_img = Image.fromarray(gray)
    config = "--psm 11 --oem 3 -c tessedit_char_whitelist=0123456789"
    text = pytesseract.image_to_string(pil_img, config=config)
    numbers = re.findall(r'\b(\d{3,5})\b', text)
    dims = [float(n) for n in numbers if 100 <= float(n) <= 30000]
    return sorted(set(dims), reverse=True)


def _shoelace_area_px2(pts):
    n = len(pts); area = 0.0
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i+1)%n]
        area += x1*y2 - x2*y1
    return abs(area) / 2.0


def _shoelace_area_m2(pts_mm):
    return _shoelace_area_px2(pts_mm) / 1e6


def _calc_confidence(pts_px, ocr_dims, known_area, calc_area, warnings):
    score = 1.0
    n = len(pts_px)
    if n > 15: score -= 0.2
    elif n > 10: score -= 0.1
    if not ocr_dims: score -= 0.2
    if known_area:
        diff = abs(calc_area - known_area) / known_area
        if diff > 0.2: score -= 0.3
        elif diff > 0.1: score -= 0.1
    score -= len(warnings) * 0.05
    return max(0.0, min(1.0, round(score, 2)))


def draw_result(image_path, result, output_path):
    img = cv2.imread(image_path)
    pts = np.array(result.pts_px, dtype=np.int32)
    cv2.polylines(img, [pts], isClosed=True, color=(0,255,0), thickness=3)
    for i, (x, y) in enumerate(result.pts_px):
        cv2.circle(img, (x, y), 6, (0,0,255), -1)
        cv2.putText(img, str(i), (x+8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,0,0), 1)
    cv2.putText(img, f"pts:{len(result.pts_px)} | {result.area_m2}m2 | conf:{result.confidence}",
                (10,30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,200,0), 2)
    cv2.imwrite(output_path, img)
