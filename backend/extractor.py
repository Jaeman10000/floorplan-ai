# extractor.py
# 도면 이미지 → 구조화된 좌표 추출
# Claude Vision API 기반 — 외곽/세대/공용부/치수/방이름 완전 분석

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

    # 1. Claude Vision API 시도
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

    # 2. OpenCV fallback
    return _extract_with_opencv(image_path, known_area_m2, scale_hint_mm_per_px,
                                 epsilon_ratio, min_area_ratio, warnings)


# ─────────────────────────────────────────────────────────────────────────────
# Claude Vision API 분석
# ─────────────────────────────────────────────────────────────────────────────

def _extract_with_vision(image_path: str, api_key: str, warnings: list) -> Optional[ExtractionResult]:
    """Claude Vision API로 도면 완전 분석"""

    # 이미지 base64 인코딩
    with open(image_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    ext = os.path.splitext(image_path)[1].lower()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp"
    }.get(ext, "image/png")

    prompt = """이 건축 도면 이미지를 분석하여 아래 JSON 형식으로 정확하게 응답하세요.
반드시 JSON만 반환하고 다른 텍스트는 포함하지 마세요.

[핵심 원칙]
픽셀 좌표가 아닌 도면의 치수선 숫자를 읽어서 실제 건축 mm 단위 좌표로 반환하라.
전체 건물 외곽, 각 세대, 공용부 모두 치수선 기준 mm 좌표로 계산하라.
좌상단을 (0,0) 기준으로 한다.
절대로 픽셀 값을 반환하지 마라. 모든 좌표는 도면에 표기된 치수선 숫자(mm)를 합산한 건축 실치수여야 한다.

분석 절차:
1. 도면에 표기된 모든 치수선 숫자(mm 단위)를 먼저 읽어라. 예: 3300, 2700, 13000 등
2. 도면 좌상단을 원점 (0, 0)으로 설정한다
3. 치수선 숫자를 수평/수직 방향으로 누적 합산하여 각 꼭짓점의 절대 mm 좌표를 계산하라
   예) 수평 치수 3300 + 2700 = 5000이면 x=0, x=3300, x=6000 순으로 꼭짓점 배치
4. 건물 전체 외곽, 각 세대(A/B/C형 등), 공용부(계단실/복도/엘리베이터홀)를 분리하라
5. 세대와 공용부는 절대 혼용하지 마라
6. 각 공간의 방 이름(거실, 침실, 욕실, 주방 등) 텍스트를 인식하라
7. 면적 숫자(m² 표기)를 읽어 area_m2에 입력하라
8. 치수선이 없거나 불분명한 경우에만 도면 비율을 추정해 mm 좌표를 계산하라

{
  "dimensions_found": [도면에서 읽은 치수선 숫자 배열 (mm 단위 정수)],
  "building_outline_mm": [[x_mm, y_mm], ...],
  "units": [
    {
      "name": "A",
      "area_m2": 숫자,
      "outline_mm": [[x_mm, y_mm], ...],
      "rooms": [
        {"name": "거실", "polygon_mm": [[x_mm, y_mm], ...], "has_window": true/false}
      ]
    }
  ],
  "common_areas": [
    {"name": "계단실", "polygon_mm": [[x_mm, y_mm], ...]},
    {"name": "복도", "polygon_mm": [[x_mm, y_mm], ...]},
    {"name": "엘리베이터홀", "polygon_mm": [[x_mm, y_mm], ...]}
  ],
  "confidence": 0.0~1.0,
  "warnings": []
}"""

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
                    {"type": "text", "text": prompt}
                ],
            }],
        },
        timeout=60.0,
    )

    if response.status_code != 200:
        raise ValueError(f"Vision API 오류: {response.status_code} {response.text[:200]}")

    raw = response.json()
    text = raw["content"][0]["text"].strip()

    # JSON 파싱
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        raise ValueError("Vision API 응답에서 JSON을 찾을 수 없음")

    data = json.loads(json_match.group())

    return _vision_data_to_result(data, warnings)


def _vision_data_to_result(data: dict, warnings: list) -> ExtractionResult:
    """Vision API JSON → ExtractionResult 변환 (치수선 기반 mm 좌표 직접 사용)"""

    # 전체 건물 외곽 — mm 좌표 직접 읽기 (픽셀 변환 없음)
    building_mm = data.get("building_outline_mm", [])
    pts_mm = [(float(p[0]), float(p[1])) for p in building_mm]
    pts_px = []  # mm 직접 모드에서는 px 좌표 불필요

    # scale_mm_per_px는 mm 모드에서 1.0으로 고정 (호환성 유지)
    scale = 1.0

    # 면적
    area_m2 = _shoelace_area_m2(pts_mm) if pts_mm else 0.0

    # 세대별 정보
    units = []
    for u in data.get("units", []):
        outline_mm_u = [(float(p[0]), float(p[1])) for p in u.get("outline_mm", [])]
        rooms = []
        for r in u.get("rooms", []):
            poly_mm = [(float(p[0]), float(p[1])) for p in r.get("polygon_mm", [])]
            rooms.append(RoomInfo(
                name=r.get("name", ""),
                polygon_mm=poly_mm,
                has_window=r.get("has_window", False),
            ))
        units.append(UnitInfo(
            name=u.get("name", ""),
            outline_mm=outline_mm_u,
            area_m2=float(u.get("area_m2", 0)),
            rooms=rooms,
        ))

    # 공용부
    common_areas = []
    for c in data.get("common_areas", []):
        poly_mm = [(float(p[0]), float(p[1])) for p in c.get("polygon_mm", [])]
        common_areas.append(CommonAreaInfo(
            name=c.get("name", ""),
            polygon_mm=poly_mm,
        ))

    warnings.extend(data.get("warnings", []))

    return ExtractionResult(
        pts_px=pts_px,
        pts_mm=pts_mm,
        scale_mm_per_px=scale,
        area_m2=round(area_m2, 2),
        confidence=float(data.get("confidence", 0.8)),
        ocr_dimensions=data.get("dimensions_found", []),
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

    # 적응형 단순화 — 사각형으로 뭉개지 않고 오각형/L자형 오목 코너까지 보존
    approx = _simplify_contour(main, epsilon_ratio, area_tol=0.02, max_vertices=24)
    pts_px = [(int(p[0][0]), int(p[0][1])) for p in approx]
    if len(pts_px) <= 4:
        warnings.append(f"단순 다각형으로 인식됨 ({len(pts_px)}각형) — 복잡 외곽이면 입력 이미지 품질 확인 필요")

    # 치수선 OCR — 도면 내 mm 숫자를 위치 정보와 함께 인식
    dim_tokens = _ocr_dimension_tokens(gray, warnings)
    dimensions = sorted({t["value"] for t in dim_tokens}, reverse=True)

    # 스케일 결정 — 우선순위: 명시 힌트 > 알려진 면적 > 치수선-변 매칭 > 이미지 너비 추정
    scale = None
    if scale_hint:
        scale = scale_hint
    elif known_area_m2:
        area_px2 = _shoelace_area_px2(pts_px)
        if area_px2 > 0:
            scale = math.sqrt(known_area_m2 * 1e6 / area_px2)
    elif dim_tokens:
        # 치수 숫자를 외곽 변에 매칭해 중앙값 스케일 산출 (오인식에 강건)
        scale = _resolve_scale_from_dimensions(pts_px, dim_tokens, warnings)

    if scale is None or scale <= 0:
        scale = 10000.0 / w
        warnings.append("스케일 자동 결정 실패 — 이미지 너비=10,000mm 가정")

    pts_mm = [(x * scale, y * scale) for x, y in pts_px]
    area_m2 = _shoelace_area_m2(pts_mm)

    # 신뢰도 — 치수선을 읽었으면 가산
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
# 유틸
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
    윤곽을 다각형으로 단순화하되, 오각형/L자형 등 오목 코너를 보존한다.

    고정 epsilon은 작은 노치(notch)를 가진 외곽을 사각형으로 뭉개버린다.
    대신 '원본 윤곽 면적과 area_tol 이내로 일치하는 가장 단순한 다각형'을
    epsilon 이진 탐색으로 찾아, 노이즈는 제거하고 실제 코너는 유지한다.
    """
    peri = cv2.arcLength(contour, True)
    true_area = cv2.contourArea(contour)
    if peri <= 0 or true_area <= 0:
        return cv2.approxPolyDP(contour, epsilon_ratio * peri, True)

    def fits(eps):
        """eps로 근사한 다각형이 면적 오차 허용 범위 안인가"""
        ap = cv2.approxPolyDP(contour, eps, True)
        if len(ap) < 3:
            return False, ap
        area = cv2.contourArea(ap)
        return abs(area - true_area) / true_area <= area_tol, ap

    # eps 범위: 둘레의 0.05% ~ 5%
    lo, hi = peri * 0.0005, peri * 0.05
    best = cv2.approxPolyDP(contour, lo, True)

    # 이진 탐색: 면적 오차를 만족하는 '가장 큰' eps(=가장 단순) 찾기
    for _ in range(24):
        mid = (lo + hi) / 2.0
        ok, ap = fits(mid)
        if ok:
            best = ap          # 더 단순화해도 면적이 유지됨 → eps 키워봄
            lo = mid
        else:
            hi = mid           # 너무 뭉개짐 → eps 줄임

    # 꼭짓점이 여전히 너무 많으면(노이즈 잔존) 단계적으로 더 단순화
    if len(best) > max_vertices:
        for k in (0.01, 0.02, 0.03):
            ap = cv2.approxPolyDP(contour, k * peri, True)
            if len(ap) <= max_vertices:
                best = ap
                break

    return best


def _configure_tesseract(pytesseract):
    """
    Tesseract 실행 파일 경로 + 언어 데이터(tessdata) 폴더 결정.

    실행 파일 우선순위: TESSERACT_CMD 환경변수 > Windows 기본 설치 경로 > PATH.
    tessdata: TESSDATA_PREFIX가 이미 있으면 그대로 두고, 없을 때만
    사용자 로컬 tessdata(%LOCALAPPDATA%\\Tesseract-OCR\\tessdata, kor 포함)를
    자동 지정한다. 이 폴더가 없는 환경(예: 집 PC)은 기본 설치 tessdata를 그대로 사용.
    """
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
        # 그 외엔 PATH 상의 tesseract 사용

    # 한글 등 추가 언어팩이 담긴 사용자 로컬 tessdata 자동 연결
    if not os.environ.get("TESSDATA_PREFIX"):
        local = os.environ.get("LOCALAPPDATA", "")
        user_tessdata = os.path.join(local, "Tesseract-OCR", "tessdata")
        if local and os.path.isdir(user_tessdata):
            os.environ["TESSDATA_PREFIX"] = user_tessdata


def _ocr_dimension_tokens(gray, warnings):
    """
    도면에서 치수 숫자를 '위치 정보와 함께' OCR.
    반환: [{"value": float(mm), "cx": px, "cy": px, "conf": float}, ...]
    좌표는 원본 gray 픽셀 기준. 미설치/실패 시 빈 리스트(파이프라인 비중단).
    """
    try:
        import pytesseract
    except ImportError:
        warnings.append("pytesseract 미설치 — 치수선 OCR 건너뜀")
        return []

    _configure_tesseract(pytesseract)

    # 치수 숫자는 작아서 원본 해상도로는 인식률이 낮다 → 2배 확대 + Otsu 이진화로 가독성 향상
    scale_up = 2
    big = cv2.resize(gray, None, fx=scale_up, fy=scale_up, interpolation=cv2.INTER_CUBIC)
    _, binimg = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        # 콤마 포함 숫자(예: "3,300", "11,700")까지 허용해 한국 도면 표기 대응
        config = "--psm 11 -c tessedit_char_whitelist=0123456789,"
        data = pytesseract.image_to_data(
            binimg, config=config, output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        warnings.append(f"Tesseract 실행 실패 — 치수선 OCR 건너뜀: {str(e)[:60]}")
        return []

    tokens = []
    n = len(data.get("text", []))
    for i in range(n):
        raw = (data["text"][i] or "").strip().replace(",", "")
        if not raw.isdigit():
            continue
        try:
            conf_val = float(data["conf"][i])
        except (ValueError, TypeError):
            conf_val = -1.0
        if conf_val < 30:          # 저신뢰 인식 제거
            continue
        value = int(raw)
        # 건축 치수로 타당한 범위(mm): 100mm ~ 100m
        if not (100 <= value <= 100000):
            continue
        # 확대 좌표 → 원본 px 좌표로 환산 (토큰 중심점)
        cx = (data["left"][i] + data["width"][i] / 2.0) / scale_up
        cy = (data["top"][i] + data["height"][i] / 2.0) / scale_up
        tokens.append({"value": float(value), "cx": cx, "cy": cy, "conf": conf_val})

    if not tokens:
        warnings.append("치수선 숫자를 인식하지 못함")
    return tokens


def _ocr_dimensions(gray, warnings):
    """치수 숫자 값 목록(중복 제거, 내림차순). 값만 필요한 호출용 래퍼."""
    tokens = _ocr_dimension_tokens(gray, warnings)
    return sorted({t["value"] for t in tokens}, reverse=True)


def _point_segment_dist(px, py, ax, ay, bx, by):
    """점 (px,py)와 선분 (ax,ay)-(bx,by) 사이 거리 및 투영 매개변수 t(0~1) 반환."""
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    tc = max(0.0, min(1.0, t))
    projx, projy = ax + tc * dx, ay + tc * dy
    return math.hypot(px - projx, py - projy), tc


def _resolve_scale_from_dimensions(pts_px, tokens, warnings):
    """
    치수 숫자를 외곽 변(edge)에 매칭해 mm/px 스케일을 추정한다.

    각 치수값 ÷ 대응 변의 픽셀 길이 = 후보 스케일.
    후보들을 '변 픽셀 길이로 가중한 합의 클러스터(consensus cluster)'로 묶어,
    가장 많은 픽셀이 지지하는 스케일을 채택한다. 긴 변일수록 1px 오차의 영향이
    작아 더 신뢰할 수 있으므로, 한두 개의 OCR 오인식(짧은 변에 흔함)을 자연히 배제한다.
    (기존: 최대 치수 ÷ bbox 한 줄 추정 → 최대값이 틀리면 전체 좌표가 왜곡됨)
    매칭 실패 시에만 최대 치수 ÷ bbox 폴백.
    """
    if not tokens or len(pts_px) < 2:
        return None

    # 외곽 변 목록 (시작점, 끝점, 길이)
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
    max_match_dist = diag * 0.10   # 변에서 이 거리 이내의 라벨만 그 변에 속한다고 간주

    # 후보 = (스케일, 가중치=변 픽셀 길이)
    candidates = []
    for tk in tokens:
        best = None  # (거리, 변 길이)
        for (ax, ay, bx, by, length) in edges:
            d, proj_t = _point_segment_dist(tk["cx"], tk["cy"], ax, ay, bx, by)
            # 치수 라벨은 변의 중앙부에 놓이므로 변 끝에 치우친 매칭은 배제
            if proj_t < 0.15 or proj_t > 0.85:
                continue
            if best is None or d < best[0]:
                best = (d, length)
        if best and best[0] <= max_match_dist:
            cand = tk["value"] / best[1]
            if cand > 0:
                candidates.append((cand, best[1]))

    if candidates:
        # 합의 클러스터: 각 후보를 기준으로 ±tol 이내 후보들의 가중치 합을 구해
        # 가장 큰 지지를 받는 후보를 중심으로 클러스터를 형성한다.
        tol = 0.15
        best_center, best_support = None, -1.0
        for c_i, _ in candidates:
            support = sum(w for c_j, w in candidates
                          if abs(c_j - c_i) / c_i <= tol)
            if support > best_support:
                best_support, best_center = support, c_i
        cluster = [(c, w) for c, w in candidates
                   if abs(c - best_center) / best_center <= tol]
        tot_w = sum(w for _, w in cluster)
        scale = sum(c * w for c, w in cluster) / tot_w   # 픽셀 길이 가중 평균
        warnings.append(
            f"치수선 {len(candidates)}개 매칭 / 합의 {len(cluster)}개 채택 "
            f"— 스케일 {scale:.3f} mm/px"
        )
        return scale

    # 폴백: 가장 큰 치수가 bbox 최대 축에 대응한다고 가정
    bbox_max = max(bbox_w, bbox_h, 1)
    max_val = max(tk["value"] for tk in tokens)
    warnings.append(
        f"치수선-변 매칭 실패 — 최대 치수 {max_val:.0f}mm / {bbox_max}px 로 추정"
    )
    return max_val / bbox_max


def _shoelace_area_px2(pts):
    n = len(pts); area = 0.0
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i+1)%n]
        area += x1*y2 - x2*y1
    return abs(area) / 2.0

def _shoelace_area_m2(pts_mm):
    return _shoelace_area_px2(pts_mm) / 1e6
