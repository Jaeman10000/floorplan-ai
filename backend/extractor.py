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

분석 규칙:
1. 이미지 픽셀 좌표(px)로 폴리곤을 추출하세요 (좌상단이 0,0)
2. 치수선의 숫자를 읽어 실제 mm 단위 스케일을 계산하세요
3. 건물 전체 외곽, 각 세대(A/B/C), 공용부(계단/복도/엘리베이터)를 완전히 분리하세요
4. 세대와 공용부는 절대 혼용하지 마세요
5. 각 공간의 방 이름(거실, 침실, 욕실 등) 텍스트를 인식하세요
6. 면적 숫자(m² 표기)를 읽어 area_m2에 입력하세요

{
  "scale_mm_per_px": 숫자,
  "image_width_px": 숫자,
  "image_height_px": 숫자,
  "dimensions_found": [치수선에서 읽은 mm 숫자 배열],
  "building_outline_px": [[x,y], ...],
  "units": [
    {
      "name": "A",
      "area_m2": 숫자,
      "outline_px": [[x,y], ...],
      "rooms": [
        {"name": "거실", "center_px": [x,y], "polygon_px": [[x,y],...], "has_window": true/false}
      ]
    }
  ],
  "common_areas": [
    {"name": "계단실", "polygon_px": [[x,y], ...]},
    {"name": "복도", "polygon_px": [[x,y], ...]},
    {"name": "엘리베이터", "polygon_px": [[x,y], ...]}
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
            "model": "claude-opus-4-5",
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
    """Vision API JSON → ExtractionResult 변환"""

    scale = float(data.get("scale_mm_per_px", 0))
    if scale <= 0:
        # 치수선 기반 스케일 재계산
        dims = data.get("dimensions_found", [])
        outline_px = data.get("building_outline_px", [])
        if dims and outline_px:
            xs = [p[0] for p in outline_px]
            ys = [p[1] for p in outline_px]
            bbox_max = max(max(xs)-min(xs), max(ys)-min(ys))
            if bbox_max > 0:
                scale = max(dims) / bbox_max
        if scale <= 0:
            scale = 10000.0 / data.get("image_width_px", 1000)
            warnings.append("스케일 계산 실패 — 이미지 너비 기준 추정")

    # 전체 건물 외곽
    building_px = data.get("building_outline_px", [])
    pts_px = [(int(p[0]), int(p[1])) for p in building_px]
    pts_mm = [(x * scale, y * scale) for x, y in pts_px]

    # 면적
    area_m2 = _shoelace_area_m2(pts_mm) if pts_mm else 0.0

    # 세대별 정보
    units = []
    for u in data.get("units", []):
        outline_px_u = u.get("outline_px", [])
        outline_mm_u = [(p[0]*scale, p[1]*scale) for p in outline_px_u]
        rooms = []
        for r in u.get("rooms", []):
            poly_px = r.get("polygon_px", [])
            poly_mm = [(p[0]*scale, p[1]*scale) for p in poly_px]
            if not poly_mm and r.get("center_px"):
                # 중심점만 있으면 임시 작은 폴리곤 생성
                cx, cy = r["center_px"]
                s = 1000  # 1m 임시
                poly_mm = [(cx*scale-s, cy*scale-s), (cx*scale+s, cy*scale-s),
                           (cx*scale+s, cy*scale+s), (cx*scale-s, cy*scale+s)]
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
        poly_px = c.get("polygon_px", [])
        poly_mm = [(p[0]*scale, p[1]*scale) for p in poly_px]
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
    _, thresh = _preprocess(img)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        raise ValueError("외곽선을 찾을 수 없습니다.")

    min_area = h * w * min_area_ratio
    valid = [c for c in contours if cv2.contourArea(c) > min_area]
    if not valid:
        valid = sorted(contours, key=cv2.contourArea, reverse=True)[:1]
        warnings.append("외곽 후보 부족 — 가장 큰 윤곽 사용")

    main = max(valid, key=cv2.contourArea)
    peri = cv2.arcLength(main, True)
    approx = cv2.approxPolyDP(main, epsilon_ratio * peri, True)
    if len(approx) > 20:
        approx = cv2.approxPolyDP(main, 0.02 * peri, True)
        warnings.append(f"꼭짓점 과다 — 단순화 ({len(approx)}개)")

    pts_px = [(int(p[0][0]), int(p[0][1])) for p in approx]

    # 스케일 결정
    scale = scale_hint or (10000.0 / w)
    if known_area_m2:
        area_px2 = _shoelace_area_px2(pts_px)
        if area_px2 > 0:
            scale = math.sqrt(known_area_m2 * 1e6 / area_px2)
    if not scale_hint and not known_area_m2:
        warnings.append("스케일 자동 결정 실패 — 이미지 너비=10,000mm 가정")

    pts_mm = [(x * scale, y * scale) for x, y in pts_px]
    area_m2 = _shoelace_area_m2(pts_mm)

    return ExtractionResult(
        pts_px=pts_px,
        pts_mm=pts_mm,
        scale_mm_per_px=scale,
        area_m2=round(area_m2, 2),
        confidence=0.4,
        ocr_dimensions=[],
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

def _shoelace_area_px2(pts):
    n = len(pts); area = 0.0
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i+1)%n]
        area += x1*y2 - x2*y1
    return abs(area) / 2.0

def _shoelace_area_m2(pts_mm):
    return _shoelace_area_px2(pts_mm) / 1e6
