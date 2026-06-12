"""
pdf_parser.py — 벡터 PDF 도면 파서 (pdfplumber)

CLAUDE.md 전략: 이미지 인식(OpenCV/Vision/딥러닝) 대신, 설계사가 보내는
벡터 PDF에서 정확한 좌표를 직접 읽는다. 추측이 없다.

────────────────────────────────────────────────────────────────────────────
테스트용.pdf(도움건축사 '지상2~3층 평면도') 실측으로 파악한 구조
────────────────────────────────────────────────────────────────────────────
• 객체 수: lines 326, rects 6, curves 1449, chars 509
• 외곽 후보를 단순히 '가장 큰 검은 curve'로 잡으면 안 된다 — 그건 stroke만 된
  대지경계선(fill=False, 8점)이라 건물보다 크다. 검증에서 확인됨.
• 건물 외벽/벽체 = '채움된(fill=True) 검은 curve' 수백 개의 '벽 strip' 조각.
  단일 외곽 curve는 없다. 이 조각들을 모아 외곽을 구성해야 한다.
  - shapely 합집합은 실패: 벽 strip들이 벡터상 미세 간격으로 떨어져 있어
    17조각으로 흩어짐(최대 15 m²).
  - 채택: 벽 조각을 고해상 래스터에 채운 뒤 closing으로 간격을 메우고
    최대 연결요소의 외곽 contour를 추적. 입력이 '정확한 벡터 벽 기하'뿐이라
    치수선·텍스트 노이즈가 없어 깨끗하다(이미지 인식과 본질적으로 다름).
• 굵기(linewidth)만으로는 벽 분류 불가: lw=1.02 선은 제목란 표, lw=0.84 선은
  대부분 2px 해칭 조각이었다. 외벽은 '선'이 아니라 '채움 curve'로 그려진다.
  내부 칸막이는 lw≈0.30 선.
• 스케일: 페이지가 정확히 A3(1191×842pt)이고 표제 'SCALE 1/100'.
  → 실제 mm = pt × (25.4/72) × 축척분모. 1/100이면 pt × 35.2778. 추정이 아니다.
• ⚠️ 도면 '내부' 면적/방 라벨(예: 58.49, 17.69)은 텍스트가 아니라 벡터
  아웃라인(curve)으로 그려져 있다. 건물 영역 안의 char = 0개. 추출 가능한
  실제 텍스트(chars)는 제목란뿐이다. 따라서 '글자 좌표 → 방 이름' 매핑은 이
  설계사 PDF에선 동작하지 않는다(방 이름은 OCR 또는 도면 규약 필요 — 미구현).

좌표계: pdfplumber 'pts'/'top'은 페이지 상단=0인 하향(y-down) 좌표.
출력은 좌상단을 (0,0)으로 정규화한 mm 좌표(y-down 유지)로 통일한다.
3D에서 위아래를 뒤집고 싶으면 소비 측에서 y를 반전한다.
"""

import re
import math
from typing import List, Tuple, Optional, Dict, Any

import numpy as np
import cv2
import pdfplumber


# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────

_PT_TO_MM = 25.4 / 72.0          # PDF 1pt = 1/72 inch
_DEFAULT_SCALE_DENOM = 100.0     # 축척 미검출 시 1/100 가정
_PAGE_SPAN_FRAC = 0.90           # 가로/세로 90% 이상 차지 = 페이지테두리(벽 아님)
_BLACK_TOL = 0.12                # non_stroking_color 각 채널 ≤ 이 값이면 '검정'
_THIN_WALL_LW = 0.20             # 이 굵기 초과 선 = 벽 후보(이하 = 보조/치수선)

# 벽 조각 래스터-합집합 파라미터
_RASTER_SCALE = 8.0              # pt → px (1px ≈ 0.125pt ≈ 4.4mm @ 1/100)
_WALL_CLOSE_PT = 8.0             # 벽 strip 간격 메우기 closing 커널(pt)
_OUTLINE_EPS_PT = 2.0            # 외곽 단순화 허용오차(pt ≈ 70mm @ 1/100)


# ─────────────────────────────────────────────────────────────────────────────
# 색/형상 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _is_black_fill(obj: dict) -> bool:
    """채움색이 검정(또는 스칼라 0)인가."""
    c = obj.get("non_stroking_color")
    if c is None:
        return False
    if isinstance(c, (int, float)):
        return abs(c) <= _BLACK_TOL
    if isinstance(c, (list, tuple)) and c:
        return all(abs(float(v)) <= _BLACK_TOL for v in c)
    return False


def _bbox_area(obj: dict) -> float:
    return (obj["x1"] - obj["x0"]) * (obj["bottom"] - obj["top"])


def _shoelace(pts: List[Tuple[float, float]]) -> float:
    """폴리곤 면적(부호 없는). pts: [(x,y),...]"""
    n = len(pts)
    s = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


# ─────────────────────────────────────────────────────────────────────────────
# 스케일
# ─────────────────────────────────────────────────────────────────────────────

def detect_scale_denominator(page) -> Tuple[float, bool]:
    """
    표제의 축척 '1/100' 또는 '1:100'을 찾아 분모를 반환.
    반환: (분모, 검출여부). 못 찾으면 (_DEFAULT_SCALE_DENOM, False).
    """
    text = " ".join((w.get("text") or "") for w in page.extract_words())
    text = text.replace(" ", "")
    m = re.search(r"1[/:](\d{1,4})", text)
    if m:
        denom = float(m.group(1))
        if denom > 0:
            return denom, True
    return _DEFAULT_SCALE_DENOM, False


def mm_per_pt(scale_denom: float) -> float:
    """PDF point → 실제 mm 환산계수."""
    return _PT_TO_MM * scale_denom


# ─────────────────────────────────────────────────────────────────────────────
# 건물 외곽
# ─────────────────────────────────────────────────────────────────────────────

def _wall_fill_curves(page) -> List[dict]:
    """
    벽체로 쓰이는 '채움된 검은 curve' 조각들. 페이지 테두리(전폭/전고 strip)와
    대지경계선(fill=False)은 제외.
    """
    W, H = float(page.width), float(page.height)
    out = []
    for c in page.curves:
        if not (c.get("fill") and _is_black_fill(c)):
            continue
        if (c["x1"] - c["x0"]) >= W * _PAGE_SPAN_FRAC:
            continue
        if (c["bottom"] - c["top"]) >= H * _PAGE_SPAN_FRAC:
            continue
        if len(c.get("pts") or []) >= 3:
            out.append(c)
    return out


def _building_outline_pts_pt(page) -> Optional[List[Tuple[float, float]]]:
    """
    건물 외곽 폴리곤 점(PDF pt, y-down)을 반환.

    수백 개 벽 strip 조각을 고해상 래스터에 채우고 closing으로 간격을 메운 뒤,
    최대 연결요소의 외곽 contour를 추적해 단순화한다. (단일 외곽 curve가 없고
    벡터 합집합은 strip 간격 때문에 실패하므로 이 방식 채택 — 헤더 주석 참조.)
    """
    walls = _wall_fill_curves(page)
    if not walls:
        return None

    W, H = float(page.width), float(page.height)
    sc = _RASTER_SCALE
    mask = np.zeros((int(H * sc) + 1, int(W * sc) + 1), np.uint8)
    for c in walls:
        poly = np.array([[int(x * sc), int(y * sc)] for x, y in c["pts"]], np.int32)
        cv2.fillPoly(mask, [poly], 255)

    k = max(3, int(_WALL_CLOSE_PT * sc) | 1)
    closed = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    )
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    main = max(contours, key=cv2.contourArea)
    eps = _OUTLINE_EPS_PT * sc
    approx = cv2.approxPolyDP(main, eps, True)
    return [(float(p[0][0]) / sc, float(p[0][1]) / sc) for p in approx]


# ─────────────────────────────────────────────────────────────────────────────
# 벽 (선 기반)
# ─────────────────────────────────────────────────────────────────────────────

def _classify_walls_pt(page) -> List[Dict[str, Any]]:
    """
    선(lines)을 굵기로 분류해 벽 후보를 반환(PDF pt, y-down).
    각 항목: {x0,y0,x1,y1,linewidth,length_pt,orient}
    굵기 ≤ _THIN_WALL_LW 는 보조선/치수선으로 보고 제외.
    """
    walls = []
    for l in page.lines:
        lw = float(l.get("linewidth") or 0)
        if lw <= _THIN_WALL_LW:
            continue
        x0, y0, x1, y1 = l["x0"], l["top"], l["x1"], l["bottom"]
        length = math.hypot(x1 - x0, y1 - y0)
        walls.append({
            "x0": float(x0), "y0": float(y0),
            "x1": float(x1), "y1": float(y1),
            "linewidth": lw,
            "length_pt": length,
            "orient": "H" if abs(y1 - y0) < abs(x1 - x0) else "V",
        })
    return walls


# ─────────────────────────────────────────────────────────────────────────────
# 제목란 메타데이터 (실제 텍스트)
# ─────────────────────────────────────────────────────────────────────────────

def _title_block_text(page) -> List[str]:
    """제목란 등 실제 텍스트 라인(읽기 순서). 내부 도면 라벨은 벡터라 안 잡힘."""
    lines = page.extract_text_lines() if hasattr(page, "extract_text_lines") else []
    out = []
    for ln in lines:
        t = (ln.get("text") or "").strip()
        if t:
            out.append(t)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────────────────

def parse_pdf(path: str, page_index: int = 0) -> Dict[str, Any]:
    """
    벡터 PDF 도면 파싱.

    반환 dict:
      scale_mm_per_pt   : float (PDF pt → mm)
      scale_denominator : float (축척 분모, 예 100)
      scale_detected    : bool  (도면에서 축척 텍스트를 찾았는지)
      building_outline_mm: [(x,y),...]  좌상단 (0,0) 정규화, y-down, mm
      outline_area_m2   : float
      walls             : [{p0:[x,y], p1:[x,y], linewidth, length_mm, orient}, ...] mm
      rooms             : []   # 미구현(내부 라벨이 벡터라 이름 매핑 불가)
      title_text        : [str, ...]  제목란 등 실제 텍스트
      page_size_pt      : [w, h]
      warnings          : [str, ...]
    """
    warnings: List[str] = []
    pdf = pdfplumber.open(path)
    if page_index >= len(pdf.pages):
        raise ValueError(f"page_index {page_index} 범위 초과 (페이지 {len(pdf.pages)}개)")
    page = pdf.pages[page_index]

    # 스케일
    denom, detected = detect_scale_denominator(page)
    if not detected:
        warnings.append(f"축척 텍스트 미검출 — 1/{int(denom)} 가정")
    s = mm_per_pt(denom)

    # 건물 외곽
    outline_pt = _building_outline_pts_pt(page)
    if not outline_pt:
        raise ValueError("건물 외곽 curve를 찾지 못함 (검은 채움 폴리곤 없음)")

    # 중복 마지막 점 제거(닫힌 path)
    if len(outline_pt) >= 2 and outline_pt[0] == outline_pt[-1]:
        outline_pt = outline_pt[:-1]

    # pt → mm, 좌상단 (0,0) 정규화
    raw_mm = [(x * s, y * s) for x, y in outline_pt]
    min_x = min(p[0] for p in raw_mm)
    min_y = min(p[1] for p in raw_mm)
    outline_mm = [(round(x - min_x, 1), round(y - min_y, 1)) for x, y in raw_mm]
    area_m2 = round(_shoelace(outline_mm) / 1e6, 2)

    # 벽/칸막이 선 (건물 bbox 안만 — 제목란·페이지테두리·대지경계 제외)
    bx0 = min(x for x, _ in outline_pt); bx1 = max(x for x, _ in outline_pt)
    by0 = min(y for _, y in outline_pt); by1 = max(y for _, y in outline_pt)
    walls = []
    for w in _classify_walls_pt(page):
        mx = (w["x0"] + w["x1"]) / 2; my = (w["y0"] + w["y1"]) / 2
        if not (bx0 <= mx <= bx1 and by0 <= my <= by1):
            continue
        walls.append({
            "p0": [round(w["x0"] * s - min_x, 1), round(w["y0"] * s - min_y, 1)],
            "p1": [round(w["x1"] * s - min_x, 1), round(w["y1"] * s - min_y, 1)],
            "linewidth": w["linewidth"],
            "length_mm": round(w["length_pt"] * s, 1),
            "orient": w["orient"],
        })

    title_text = _title_block_text(page)

    # 내부 라벨이 벡터인지 점검: 외곽 bbox 안의 실제 char 수
    bx0 = min(x for x, _ in outline_pt); bx1 = max(x for x, _ in outline_pt)
    by0 = min(y for _, y in outline_pt); by1 = max(y for _, y in outline_pt)
    inside_chars = sum(
        1 for c in page.chars
        if bx0 < (c["x0"] + c["x1"]) / 2 < bx1 and by0 < (c["top"] + c["bottom"]) / 2 < by1
    )
    if inside_chars == 0:
        warnings.append(
            "도면 내부 텍스트 char=0 — 방/면적 라벨이 벡터 아웃라인으로 추정. "
            "방 이름 매핑 불가(OCR 또는 도면 규약 필요)."
        )

    return {
        "scale_mm_per_pt": round(s, 4),
        "scale_denominator": denom,
        "scale_detected": detected,
        "building_outline_mm": outline_mm,
        "outline_area_m2": area_m2,
        "walls": walls,
        "rooms": [],  # 미구현
        "title_text": title_text,
        "page_size_pt": [round(float(page.width), 1), round(float(page.height), 1)],
        "warnings": warnings,
    }


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")
    path = sys.argv[1] if len(sys.argv) > 1 else "테스트용.pdf"
    r = parse_pdf(path)
    r_print = dict(r)
    r_print["building_outline_mm"] = f"{len(r['building_outline_mm'])}점"
    r_print["walls"] = f"{len(r['walls'])}개"
    r_print["title_text"] = f"{len(r['title_text'])}줄"
    print(json.dumps(r_print, ensure_ascii=False, indent=2))
    print("\n외곽 mm:", r["building_outline_mm"])
    print("면적:", r["outline_area_m2"], "m²")
