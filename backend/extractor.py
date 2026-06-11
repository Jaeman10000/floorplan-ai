# extractor.py
# 도면 이미지 → 구조화된 좌표 추출
# Claude Vision API: 치수선 숫자 읽기 → 변 길이 기반 좌표 계산

import cv2
import numpy as np
import base64
import json
import math
import os
import re
import httpx
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 구조
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RoomInfo:
    name: str                              # "거실", "침실" 등
    polygon_mm: List[Tuple[float, float]]  # mm 좌표
    has_window: bool = False

@dataclass
class UnitInfo:
    name: str                              # "A", "B", "C"
    outline_mm: List[Tuple[float, float]]  # 세대 외곽 mm
    area_m2: float = 0.0
    rooms: List[RoomInfo] = field(default_factory=list)

@dataclass
class CommonAreaInfo:
    name: str                              # "계단실", "복도", "엘리베이터"
    polygon_mm: List[Tuple[float, float]]

@dataclass
class ExtractionResult:
    # 전체 건물 외곽
    pts_px: List[Tuple[int, int]]
    pts_mm: List[Tuple[float, float]]
    scale_mm_per_px: float
    area_m2: float
    confidence: float
    ocr_dimensions: List[float]
    warnings: List[str]

    # 세대별 정보
    units: List[UnitInfo] = field(default_factory=list)

    # 공용부
    common_areas: List[CommonAreaInfo] = field(default_factory=list)

    # 원본 Vision 응답 (디버그용)
    raw_vision: Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────────────
# 건물 외곽 폴리곤 계산 (코드가 모든 계산 담당)
# ─────────────────────────────────────────────────────────────────────────────

def build_pentagon(
    top_dims: list,
    left_dims: list,
    has_diagonal: bool = False,
    diag_dx: float = 0.0,
    diag_dy: float = 0.0,
    H_override: Optional[float] = None,
) -> Tuple[List[Tuple[float, float]], dict]:
    """
    상단·좌측 치수 합산 + 우하단 사선 크기 → 건물 외곽 폴리곤.

    has_diagonal=False → 직사각형 4꼭짓점:
      P0=(0,0)  P1=(W,0)  P2=(W,H)  P3=(0,H)

    has_diagonal=True  → 우하단 삼각형 잘린 오각형 5꼭짓점:
      P0=(0,0)  P1=(W,0)  P2=(W, H-diag_dy)  P3=(W-diag_dx, H)  P4=(0,H)

    diag_dx: 사선의 가로 길이 (양수, 우→좌 방향)
    diag_dy: 사선의 세로 길이 (양수, 하→상 방향)
    H_override: 세로 총 치수 보정값 (None이면 sum(left_dims) 사용)
    """
    W = sum(float(v) for v in top_dims)
    H = H_override if H_override is not None else sum(float(v) for v in left_dims)

    if has_diagonal and (diag_dx > 0 or diag_dy > 0):
        pts = [
            (0.0,          0.0),
            (W,            0.0),
            (W,            H - diag_dy),
            (W - diag_dx,  H),
            (0.0,          H),
        ]
    else:
        pts = [
            (0.0, 0.0),
            (W,   0.0),
            (W,   H),
            (0.0, H),
        ]

    stats = {"W": W, "H": H, "diag_dx": diag_dx, "diag_dy": diag_dy,
             "has_diagonal": has_diagonal}
    return pts, stats


# ─────────────────────────────────────────────────────────────────────────────
# 메인 추출 함수
# ─────────────────────────────────────────────────────────────────────────────

def extract_outline(
    image_path: str,
    known_area_m2: Optional[float] = None,
    scale_hint_mm_per_px: Optional[float] = None,
    epsilon_ratio: float = 0.01,
    min_area_ratio: float = 0.05,
) -> ExtractionResult:
    """
    도면 이미지를 분석하여 ExtractionResult 반환.
    Claude Vision API 사용 → 실패 시 OpenCV fallback.
    """
    warnings: List[str] = []

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            result = _extract_with_vision(image_path, api_key, warnings)
            if result:
                return result
        except Exception as e:
            warnings.append(f"Vision API 실패, OpenCV fallback: {str(e)[:80]}")
    else:
        warnings.append("ANTHROPIC_API_KEY 없음 — OpenCV fallback 사용")

    return _extract_with_opencv(image_path, known_area_m2, scale_hint_mm_per_px,
                                 epsilon_ratio, min_area_ratio, warnings)


# ─────────────────────────────────────────────────────────────────────────────
# Claude Vision API — 숫자 읽기 전담 (계산 없음)
# ─────────────────────────────────────────────────────────────────────────────

_VISION_PROMPT = """\
이 건축 도면 이미지에서 치수선 숫자를 읽어 JSON으로만 반환하라.
JSON 외 다른 텍스트는 절대 포함하지 마라.
계산하지 말 것. 도면에 인쇄된 숫자만 그대로 읽어라.

[읽어야 할 항목]
• top_dims    : 건물 상단 외벽의 치수 숫자, 왼→오른 순서, mm 정수
• left_dims   : 건물 좌측 외벽의 치수 숫자, 위→아래 순서, mm 정수 (개별 구간 치수만, 전체 합계 치수는 제외)
• building_height_mm: 건물 좌측 외벽 전체 높이를 나타내는 치수 (left_dims 구간들의 합계로 표시된 총 치수, mm 정수, 없으면 0)
• has_diagonal: 건물 외곽에 사선 벽이 있으면 true, 직사각형이면 false
• diagonal_horizontal_mm: 사선의 가로 길이 (has_diagonal=true일 때만, mm 정수)
• diagonal_vertical_mm  : 사선의 세로 길이 (has_diagonal=true일 때만, mm 정수)
• units       : 세대 이름, 면적(m²), 방 이름 목록
• common_areas: 공용부 이름 목록 (계단실, 엘리베이터홀, 복도 등)
• common_area_m2: 공용부 면적 합계 (없으면 0)

[주의 사항]
1. right_dims와 bottom_dims는 반환하지 마라.
2. 상단(top_dims)과 좌측(left_dims) 치수선만 읽는다.
3. 건물 외벽에 붙은 치수선만 읽는다. 대지 경계선 치수는 무시한다.
4. 59.76, 65.21 같은 소수점 방 면적은 치수 배열에 넣지 않는다.
5. 세대(units): 거실·침실이 있고 면적 30m² 이상인 주거 단위만.
6. 18m² 이하 공간은 절대 units에 포함하지 않는다.

[응답 형식]
{
  "top_dims":  [3000, 3300, 3300, 3400],
  "left_dims": [1300, 2700, 2700, 5200, 2700, 3300, 1400],
  "building_height_mm": 19300,
  "has_diagonal": true,
  "diagonal_horizontal_mm": 3400,
  "diagonal_vertical_mm":   2900,
  "units": [
    {"name": "A", "area_m2": 59.76, "rooms": ["거실", "침실", "침실", "욕실", "주방"]},
    {"name": "B", "area_m2": 65.21, "rooms": ["거실", "침실", "침실", "침실", "욕실", "주방"]},
    {"name": "C", "area_m2": 64.00, "rooms": ["거실", "침실", "침실", "욕실", "주방", "발코니"]}
  ],
  "common_areas": ["계단실", "엘리베이터홀"],
  "common_area_m2": 18.41,
  "confidence": 0.0~1.0,
  "warnings": []
}"""


def _extract_with_vision(image_path: str, api_key: str, warnings: list) -> Optional[ExtractionResult]:
    """Claude Vision API 호출 — 치수 숫자 읽기 전담"""

    with open(image_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    ext = os.path.splitext(image_path)[1].lower()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/png")

    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-opus-4-8",
            "max_tokens": 4096,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": img_data},
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }],
        },
        timeout=60.0,
    )

    if response.status_code != 200:
        raise ValueError(f"Vision API 오류: {response.status_code} {response.text[:200]}")

    text = response.json()["content"][0]["text"].strip()
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        raise ValueError("Vision API 응답에서 JSON을 찾을 수 없음")

    data = json.loads(json_match.group())
    return _vision_data_to_result(data, warnings)


def _vision_data_to_result(data: dict, warnings: list) -> ExtractionResult:
    """Vision JSON → build_pentagon() 호출 → ExtractionResult"""

    top_dims  = [float(v) for v in data.get("top_dims",  [])]
    left_dims = [float(v) for v in data.get("left_dims", [])]

    if not (top_dims and left_dims):
        warnings.append("치수 데이터 없음 — 좌표 계산 불가")
        pts_mm: List[Tuple[float, float]] = []
        stats: dict = {}
    else:
        W = sum(top_dims)
        H_raw = sum(left_dims)
        H_override: Optional[float] = None

        # ── left_dims 합계 범위 검증 ──────────────────────────────────────────
        if W > 0:
            ratio = H_raw / W
            if ratio < 0.5:
                warnings.append(
                    f"left_dims 합 {H_raw:.0f}mm < 가로 {W:.0f}mm × 0.5 "
                    f"({ratio:.2f}×) — 세로 치수 누락 의심"
                )
            elif ratio > 3.0:
                warnings.append(
                    f"left_dims 합 {H_raw:.0f}mm > 가로 {W:.0f}mm × 3 "
                    f"({ratio:.2f}×) — 세로 치수 중복 의심"
                )

        # ── 전체 높이 치수로 자동 보정 ────────────────────────────────────────
        bh = float(data.get("building_height_mm", 0))
        if bh > 0:
            diff = abs(H_raw - bh)
            if diff > bh * 0.05:
                warnings.append(
                    f"left_dims 합 {H_raw:.0f}mm ≠ 전체 높이 {bh:.0f}mm "
                    f"(차이 {diff:.0f}mm, {diff/bh*100:.1f}%) — 전체 높이로 자동 보정"
                )
                H_override = bh

        has_diag = bool(data.get("has_diagonal", False))
        diag_dx  = float(data.get("diagonal_horizontal_mm", 0))
        diag_dy  = float(data.get("diagonal_vertical_mm",   0))
        pts_mm, stats = build_pentagon(
            top_dims, left_dims, has_diag, diag_dx, diag_dy, H_override
        )

        if has_diag:
            warnings.append(
                f"사선: 가로 {diag_dx:.0f}mm × 세로 {diag_dy:.0f}mm "
                f"(P2=({stats['W']:.0f},{stats['H']-diag_dy:.0f}) → "
                f"P3=({stats['W']-diag_dx:.0f},{stats['H']:.0f}))"
            )

    area_m2 = _shoelace_area_m2(pts_mm) if pts_mm else 0.0

    # 세대
    units: List[UnitInfo] = []
    for u in data.get("units", []):
        area = float(u.get("area_m2", 0))
        if area < 30:
            warnings.append(f"세대 후보 {u.get('name','')} {area:.1f}m² 제외 (30m² 미만)")
            continue
        rooms = [RoomInfo(name=str(r), polygon_mm=[], has_window=False)
                 for r in u.get("rooms", [])]
        units.append(UnitInfo(
            name=u.get("name", ""),
            outline_mm=[],
            area_m2=area,
            rooms=rooms,
        ))

    if len(units) != 3:
        warnings.append(f"세대 수 {len(units)}개 — 예상 3개(A/B/C)와 다름")

    # 공용부
    common_areas: List[CommonAreaInfo] = [
        CommonAreaInfo(name=str(c), polygon_mm=[])
        for c in data.get("common_areas", [])
    ]

    warnings.extend(data.get("warnings", []))

    all_dims = top_dims + left_dims

    return ExtractionResult(
        pts_px=[],
        pts_mm=pts_mm,
        scale_mm_per_px=1.0,
        area_m2=round(area_m2, 2),
        confidence=float(data.get("confidence", 0.8)),
        ocr_dimensions=sorted(set(all_dims), reverse=True),
        warnings=warnings,
        units=units,
        common_areas=common_areas,
        raw_vision=data,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OpenCV fallback
# ─────────────────────────────────────────────────────────────────────────────

def _extract_with_opencv(
    image_path, known_area_m2, scale_hint, epsilon_ratio, min_area_ratio, warnings
) -> ExtractionResult:
    """OpenCV 기반 외곽선 추출 (Vision API 실패 시 fallback)"""

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"이미지를 읽을 수 없습니다: {image_path}")

    h, w = img.shape[:2]
    gray, thresh = _preprocess(img)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        raise ValueError("외곽선을 찾을 수 없습니다.")

    min_area = h * w * min_area_ratio
    valid = [c for c in contours if cv2.contourArea(c) > min_area]
    if not valid:
        valid = sorted(contours, key=cv2.contourArea, reverse=True)[:1]
        warnings.append("외곽 후보 부족 — 가장 큰 윤곽 사용")

    main = max(valid, key=cv2.contourArea)
    approx = _simplify_contour(main, epsilon_ratio, area_tol=0.02, max_vertices=24)
    pts_px = [(int(p[0][0]), int(p[0][1])) for p in approx]
    if len(pts_px) <= 4:
        warnings.append(f"단순 다각형으로 인식됨 ({len(pts_px)}각형)")

    dim_tokens = _ocr_dimension_tokens(gray, warnings)
    dimensions = sorted({t["value"] for t in dim_tokens}, reverse=True)

    scale = None
    if scale_hint:
        scale = scale_hint
    elif known_area_m2:
        area_px2 = _shoelace_area_px2(pts_px)
        if area_px2 > 0:
            scale = math.sqrt(known_area_m2 * 1e6 / area_px2)
    elif dim_tokens:
        scale = _resolve_scale_from_dimensions(pts_px, dim_tokens, warnings)

    if scale is None or scale <= 0:
        scale = 10000.0 / w
        warnings.append("스케일 자동 결정 실패 — 이미지 너비=10,000mm 가정")

    pts_mm = [(x * scale, y * scale) for x, y in pts_px]
    area_m2 = _shoelace_area_m2(pts_mm)

    confidence = 0.4
    if dimensions:
        confidence = 0.6
    if scale_hint or known_area_m2:
        confidence = 0.65

    return ExtractionResult(
        pts_px=pts_px,
        pts_mm=pts_mm,
        scale_mm_per_px=scale,
        area_m2=round(area_m2, 2),
        confidence=confidence,
        ocr_dimensions=dimensions,
        warnings=warnings,
        units=[],
        common_areas=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# server.py 응답 직렬화 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def result_to_dict(result: ExtractionResult) -> dict:
    """ExtractionResult → JSON 직렬화 가능한 dict"""
    return {
        "pts_px": result.pts_px,
        "pts_mm": result.pts_mm,
        "scale_mm_per_px": result.scale_mm_per_px,
        "area_m2": result.area_m2,
        "confidence": result.confidence,
        "ocr_dimensions": result.ocr_dimensions,
        "warnings": result.warnings,
        "units": [
            {
                "name": u.name,
                "area_m2": u.area_m2,
                "outline_mm": u.outline_mm,
                "rooms": [
                    {
                        "name": r.name,
                        "polygon_mm": r.polygon_mm,
                        "has_window": r.has_window,
                    }
                    for r in u.rooms
                ],
            }
            for u in result.units
        ],
        "common_areas": [
            {
                "name": c.name,
                "polygon_mm": c.polygon_mm,
            }
            for c in result.common_areas
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# OpenCV 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _preprocess(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)
    return gray, opened


def _simplify_contour(contour, epsilon_ratio=0.01, area_tol=0.02, max_vertices=24):
    """
    오각형/L자형 등 오목 코너를 보존하는 적응형 다각형 단순화.
    이진 탐색으로 '원본 면적 오차 area_tol 이내를 만족하는 가장 단순한 다각형' 탐색.
    """
    peri = cv2.arcLength(contour, True)
    true_area = cv2.contourArea(contour)
    if peri <= 0 or true_area <= 0:
        return cv2.approxPolyDP(contour, epsilon_ratio * peri, True)

    def fits(eps):
        ap = cv2.approxPolyDP(contour, eps, True)
        if len(ap) < 3:
            return False, ap
        area = cv2.contourArea(ap)
        return abs(area - true_area) / true_area <= area_tol, ap

    lo, hi = peri * 0.0005, peri * 0.05
    best = cv2.approxPolyDP(contour, lo, True)

    for _ in range(24):
        mid = (lo + hi) / 2.0
        ok, ap = fits(mid)
        if ok:
            best = ap
            lo = mid
        else:
            hi = mid

    if len(best) > max_vertices:
        for k in (0.01, 0.02, 0.03):
            ap = cv2.approxPolyDP(contour, k * peri, True)
            if len(ap) <= max_vertices:
                best = ap
                break

    return best


def _configure_tesseract(pytesseract):
    tess_cmd = os.environ.get("TESSERACT_CMD")
    if tess_cmd and os.path.exists(tess_cmd):
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    else:
        for cand in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if os.path.exists(cand):
                pytesseract.pytesseract.tesseract_cmd = cand
                break

    if not os.environ.get("TESSDATA_PREFIX"):
        local = os.environ.get("LOCALAPPDATA", "")
        user_tessdata = os.path.join(local, "Tesseract-OCR", "tessdata")
        if local and os.path.isdir(user_tessdata):
            os.environ["TESSDATA_PREFIX"] = user_tessdata


def _ocr_dimension_tokens(gray, warnings):
    """
    도면에서 치수 숫자를 위치 정보와 함께 OCR.
    반환: [{"value": float(mm), "cx": px, "cy": px, "conf": float}, ...]
    미설치/바이너리 없음 시 빈 리스트 반환 (파이프라인 비중단).
    """
    try:
        import pytesseract
    except ImportError:
        warnings.append("pytesseract 미설치 — 치수선 OCR 건너뜀")
        return []

    _configure_tesseract(pytesseract)

    try:
        pytesseract.get_tesseract_version()
    except Exception:
        warnings.append("Tesseract 바이너리 없음 — 치수선 OCR 건너뜀 (Vision API로 처리)")
        return []

    scale_up = 2
    big = cv2.resize(gray, None, fx=scale_up, fy=scale_up, interpolation=cv2.INTER_CUBIC)
    _, binimg = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        config = "--psm 11 -c tessedit_char_whitelist=0123456789,"
        data = pytesseract.image_to_data(
            binimg, config=config, output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        warnings.append(f"Tesseract 실행 실패: {str(e)[:60]}")
        return []

    tokens = []
    for i in range(len(data.get("text", []))):
        raw = (data["text"][i] or "").strip().replace(",", "")
        if not raw.isdigit():
            continue
        try:
            conf_val = float(data["conf"][i])
        except (ValueError, TypeError):
            conf_val = -1.0
        if conf_val < 30:
            continue
        value = int(raw)
        if not (100 <= value <= 100000):
            continue
        cx = (data["left"][i] + data["width"][i] / 2.0) / scale_up
        cy = (data["top"][i] + data["height"][i] / 2.0) / scale_up
        tokens.append({"value": float(value), "cx": cx, "cy": cy, "conf": conf_val})

    if not tokens:
        warnings.append("치수선 숫자를 인식하지 못함")
    return tokens


def _point_segment_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    tc = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + tc * dx), py - (ay + tc * dy)), tc


def _resolve_scale_from_dimensions(pts_px, tokens, warnings):
    """치수 숫자를 외곽 변에 매칭해 mm/px 스케일 추정 (합의 클러스터 방식)."""
    if not tokens or len(pts_px) < 2:
        return None

    n = len(pts_px)
    edges = []
    for i in range(n):
        ax, ay = pts_px[i]
        bx, by = pts_px[(i + 1) % n]
        length = math.hypot(bx - ax, by - ay)
        if length > 0:
            edges.append((ax, ay, bx, by, length))
    if not edges:
        return None

    xs = [p[0] for p in pts_px]
    ys = [p[1] for p in pts_px]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)
    diag = math.hypot(bbox_w, bbox_h)
    max_match_dist = diag * 0.10

    candidates = []
    for tk in tokens:
        best = None
        for (ax, ay, bx, by, length) in edges:
            d, proj_t = _point_segment_dist(tk["cx"], tk["cy"], ax, ay, bx, by)
            if proj_t < 0.15 or proj_t > 0.85:
                continue
            if best is None or d < best[0]:
                best = (d, length)
        if best and best[0] <= max_match_dist:
            cand = tk["value"] / best[1]
            if cand > 0:
                candidates.append((cand, best[1]))

    if candidates:
        tol = 0.15
        best_center, best_support = None, -1.0
        for c_i, _ in candidates:
            support = sum(w for c_j, w in candidates if abs(c_j - c_i) / c_i <= tol)
            if support > best_support:
                best_support, best_center = support, c_i
        cluster = [(c, w) for c, w in candidates
                   if abs(c - best_center) / best_center <= tol]
        tot_w = sum(w for _, w in cluster)
        scale = sum(c * w for c, w in cluster) / tot_w
        warnings.append(
            f"치수선 {len(candidates)}개 매칭 / 합의 {len(cluster)}개 채택 — 스케일 {scale:.3f} mm/px"
        )
        return scale

    bbox_max = max(bbox_w, bbox_h, 1)
    max_val = max(tk["value"] for tk in tokens)
    warnings.append(f"치수선-변 매칭 실패 — 최대 치수 {max_val:.0f}mm / {bbox_max}px 추정")
    return max_val / bbox_max


def _shoelace_area_px2(pts):
    n = len(pts)
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _shoelace_area_m2(pts_mm):
    return _shoelace_area_px2(pts_mm) / 1e6
