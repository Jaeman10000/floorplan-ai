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
# 변 목록 → 좌표 변환 (핵심 함수)
# ─────────────────────────────────────────────────────────────────────────────

def edges_to_polygon(
    edges: list,
    origin: Tuple[float, float] = (0.0, 0.0),
    close_tolerance_mm: float = 100.0,
) -> List[Tuple[float, float]]:
    """
    변 목록(direction + length_mm) → 절대 mm 좌표 목록.

    direction 규칙 (도면 좌표계: x→오른쪽, y↓아래):
      "right"    : x += length_mm
      "left"     : x -= length_mm
      "down"     : y += length_mm
      "up"       : y -= length_mm
      "diagonal" : x += dx, y += dy  (dx/dy는 부호 포함 mm 실수)

    마지막 점이 시작점과 close_tolerance_mm 이내이면 폐합점 제거.
    """
    if not edges:
        return []

    pts: List[Tuple[float, float]] = [origin]
    x, y = float(origin[0]), float(origin[1])

    for edge in edges:
        direction = str(edge.get("direction", "")).lower().strip()
        length = float(edge.get("length_mm", 0))

        if direction == "right":
            x += length
        elif direction == "left":
            x -= length
        elif direction == "down":
            y += length
        elif direction == "up":
            y -= length
        elif direction == "diagonal":
            x += float(edge.get("dx", 0))
            y += float(edge.get("dy", 0))
        else:
            continue

        pts.append((x, y))

    # 폴리곤 닫힘 처리
    if len(pts) > 1:
        dist = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if dist <= close_tolerance_mm:
            # 마지막 점이 시작점과 동일 → 중복 제거
            pts = pts[:-1]
        # dist > close_tolerance_mm: Vision이 폐합 변을 누락한 경우
        # shoelace 공식은 암묵적으로 pts[-1]→pts[0]을 닫으므로 pts 그대로 사용

    return pts


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
    warnings = []

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
# Claude Vision API 분석 (치수선 기반)
# ─────────────────────────────────────────────────────────────────────────────

_VISION_PROMPT = """\
이 건축 도면 이미지를 분석하여 아래 JSON 형식으로만 응답하라.
JSON 외 다른 텍스트는 절대 포함하지 마라.

━━━ 핵심 원칙 ━━━
1. 픽셀 좌표 추정 절대 금지 — 도면에 인쇄된 치수선 숫자만 읽는다.
2. 좌상단 꼭짓점에서 시계방향으로 각 변을 순서대로 열거한다.
3. 각 변은 진행 방향(right/left/up/down/diagonal)과 치수선 mm 값으로 표현한다.
4. 치수선이 없는 변: 인접한 전체 치수 − 나머지 부분 치수 합으로 계산해 채운다.
   (예: 전체 가로 13000, 좌측 부분 5000 → 나머지 변 = 8000)
5. 사선 변은 "diagonal"로 표기하고 dx, dy에 부호 포함 mm 값을 입력한다.

━━━ 분석 절차 ━━━
Step 1. 도면의 모든 치수선 숫자(mm)를 읽어 dimensions_found에 기록한다.
Step 2. 건물 전체 외곽을 좌상단에서 시계방향으로 추적한다.
        각 변: direction + length_mm (사선이면 추가로 dx, dy).
Step 3. 각 세대(A형/B형 등)와 공용부(계단실/복도/엘리베이터홀)를 동일 방식으로 분석한다.
        세대/공용부는 각자의 좌상단에서 시계방향으로 추적한다.
Step 4. 방 이름 텍스트(거실·침실·욕실·주방 등)와 면적 숫자(m²)를 인식한다.
Step 5. 추적 완료 후 폐합 검증: 모든 dx 합산=0, 모든 dy 합산=0 이어야 한다.
        닫히지 않으면 누락된 마지막 변을 계산해 반드시 추가한다.
        예) 우측 하강 합계 17500, 좌측 상승 합계 16600 → 마지막 "up 900" 추가.

━━━ 응답 형식 ━━━
{
  "building_edges": [
    {"direction": "right", "length_mm": 13000},
    {"direction": "down",  "length_mm": 8500},
    {"direction": "diagonal", "length_mm": 3606, "dx": -2500, "dy": -2700},
    ...
  ],
  "units": [
    {
      "name": "A",
      "area_m2": 59.76,
      "edges": [
        {"direction": "right", "length_mm": 7000},
        ...
      ],
      "rooms": [
        {
          "name": "거실",
          "has_window": true,
          "edges": [{"direction": "right", "length_mm": 3300}, ...]
        }
      ]
    }
  ],
  "common_areas": [
    {
      "name": "계단실",
      "edges": [{"direction": "right", "length_mm": 2400}, ...]
    }
  ],
  "dimensions_found": [13000, 8500, 7000, 3300],
  "confidence": 0.0~1.0,
  "warnings": []
}"""


def _extract_with_vision(image_path: str, api_key: str, warnings: list) -> Optional[ExtractionResult]:
    """Claude Vision API로 도면 완전 분석 (치수선 기반 변 길이 방식)"""

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
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data,
                        },
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }],
        },
        timeout=60.0,
    )

    if response.status_code != 200:
        raise ValueError(f"Vision API 오류: {response.status_code} {response.text[:200]}")

    raw = response.json()
    text = raw["content"][0]["text"].strip()

    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        raise ValueError("Vision API 응답에서 JSON을 찾을 수 없음")

    data = json.loads(json_match.group())
    return _vision_data_to_result(data, warnings)


def _vision_data_to_result(data: dict, warnings: list) -> ExtractionResult:
    """Vision API JSON (edges 기반) → ExtractionResult"""

    # 건물 외곽: edges → 좌표
    building_edges = data.get("building_edges", [])
    pts_mm = edges_to_polygon(building_edges)

    if not pts_mm:
        warnings.append("building_edges가 비어있음 — 좌표 계산 불가")

    area_m2 = _shoelace_area_m2(pts_mm) if pts_mm else 0.0

    # 세대별 정보
    units: List[UnitInfo] = []
    for u in data.get("units", []):
        unit_edges = u.get("edges", [])
        unit_outline = edges_to_polygon(unit_edges)
        rooms: List[RoomInfo] = []
        for r in u.get("rooms", []):
            room_poly = edges_to_polygon(r.get("edges", []))
            rooms.append(RoomInfo(
                name=r.get("name", ""),
                polygon_mm=room_poly,
                has_window=bool(r.get("has_window", False)),
            ))
        units.append(UnitInfo(
            name=u.get("name", ""),
            outline_mm=unit_outline,
            area_m2=float(u.get("area_m2", 0)),
            rooms=rooms,
        ))

    # 공용부
    common_areas: List[CommonAreaInfo] = []
    for c in data.get("common_areas", []):
        poly = edges_to_polygon(c.get("edges", []))
        common_areas.append(CommonAreaInfo(
            name=c.get("name", ""),
            polygon_mm=poly,
        ))

    warnings.extend(data.get("warnings", []))

    return ExtractionResult(
        pts_px=[],
        pts_mm=pts_mm,
        scale_mm_per_px=1.0,
        area_m2=round(area_m2, 2),
        confidence=float(data.get("confidence", 0.8)),
        ocr_dimensions=[float(d) for d in data.get("dimensions_found", [])],
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
