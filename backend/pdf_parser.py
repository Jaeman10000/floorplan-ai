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

# 방 구획(planar subdivision) 파라미터
_ROOM_MIN_AREA_M2 = 1.0          # 이 면적 미만 자유공간은 방으로 안 침(틈/노이즈)
_ROOM_EPS_PT = 1.5               # 방 폴리곤 단순화 허용오차(pt)
_ROOM_OPEN_PX = 5                # 자유공간 잡음 제거 opening 커널(px)

# 벽 표현 방식 분기 / stroke 기반(선으로 그린 벽) 경로 파라미터
_MIN_BUILDING_M2 = 30.0          # fills 외곽 면적이 이 미만이면 stroke 경로로 전환
_BIG_ELEM_FRAC = 0.7             # 자기 bbox가 페이지의 이 비율↑ 단일요소=대지경계/테두리 제외
_WALL_SEAL_PT = 1.5              # stroke 벽 이음매 봉합 closing(pt)
_BUILDING_MERGE_PT = 15.0        # 방들을 건물 한 덩어리로 묶는 closing(pt) — 가짜방 필터용

# 가구/설비 필터 (식탁·싱크대·조리대·신발장·욕조·변기 등 — 구조벽 아님, 3D 제외)
_FURN_BLOB_MM = 250.0            # opening 커널: 두 방향 모두 이보다 굵은 솔리드 = 설비/가구
                                 # 덩어리(욕조·변기·세면대·싱크대·식탁). 가는 구조벽 제외
_FURN_MAX_M2 = 4.0              # 이보다 큰 솔리드 덩어리는 가구로 안 봄(놓친 방·구조 보호)
_FURN_RASTER_PXMM = 0.1        # 가구 병합용 래스터 해상도(px/mm) — 10mm/px

# 방 이름 텍스트 키워드 (실제 텍스트로 들어있는 도면용)
_ROOM_NAME_KEYWORDS = ["거실", "침실", "욕실", "주방", "현관",
                       "발코니", "파우더룸", "다용도실", "테라스"]


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


def _is_black_stroke(obj: dict) -> bool:
    """선 색(stroking_color)이 검정(또는 스칼라 0)인가."""
    c = obj.get("stroking_color")
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
# 방 구획 (planar subdivision)
# ─────────────────────────────────────────────────────────────────────────────

def _wall_mask_raster(page, sc: float, shape) -> np.ndarray:
    """
    벽 마스크(래스터): 채움 벽 curve(외벽+벽체) + 검은 칸막이 선(lw>임계).
    회색 치수선/그리드선은 검정이 아니므로 자연히 제외된다.
    """
    wall = np.zeros(shape, np.uint8)
    for c in _wall_fill_curves(page):
        poly = np.array([[int(x * sc), int(y * sc)] for x, y in c["pts"]], np.int32)
        cv2.fillPoly(wall, [poly], 255)
    for l in page.lines:
        lw = float(l.get("linewidth") or 0)
        if lw <= _THIN_WALL_LW or not _is_black_stroke(l):
            continue
        cv2.line(
            wall,
            (int(l["x0"] * sc), int(l["top"] * sc)),
            (int(l["x1"] * sc), int(l["bottom"] * sc)),
            255, thickness=max(2, int(lw * sc)),
        )
    return wall


def _extract_rooms_pt(page, outline_pt: List[Tuple[float, float]], s: float) -> List[dict]:
    """
    벽 선들의 planar subdivision으로 건물 내부를 방 단위로 분리.
    방 = (건물 내부 마스크) − (벽 마스크)의 연결요소.

    반환: [{contour_pt: [(x,y)...], area_pt2: float}, ...] (PDF pt, y-down)
    ⚠️ 문 개구부에서 벽이 끊기면 인접 공간이 한 방으로 병합될 수 있다(v1 한계).
    """
    W, H = float(page.width), float(page.height)
    sc = _RASTER_SCALE
    shape = (int(H * sc) + 1, int(W * sc) + 1)

    wall = _wall_mask_raster(page, sc, shape)
    bmask = np.zeros(shape, np.uint8)
    bpoly = np.array([[int(x * sc), int(y * sc)] for x, y in outline_pt], np.int32)
    cv2.fillPoly(bmask, [bpoly], 255)

    free = cv2.bitwise_and(bmask, cv2.bitwise_not(wall))
    if _ROOM_OPEN_PX > 0:
        free = cv2.morphologyEx(
            free, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (_ROOM_OPEN_PX, _ROOM_OPEN_PX)),
        )

    return _free_components_to_rooms(free, sc, s)


# ─────────────────────────────────────────────────────────────────────────────
# 방 이름 텍스트 (벡터 라벨이 아니라 진짜 텍스트인 도면용)
# ─────────────────────────────────────────────────────────────────────────────

def _room_name_words(page) -> List[dict]:
    """
    방 이름 키워드와 일치하는 단어 + 중심 좌표(PDF pt, y-down).
    내부 라벨이 벡터 곡선인 도면(테스트용.pdf)에선 빈 리스트가 나온다.
    반환: [{text, cx, cy}, ...]
    """
    out = []
    for w in page.extract_words():
        t = (w.get("text") or "").strip()
        if not t:
            continue
        for kw in _ROOM_NAME_KEYWORDS:
            if kw in t:
                out.append({
                    "text": t,
                    "cx": (float(w["x0"]) + float(w["x1"])) / 2.0,
                    "cy": (float(w["top"]) + float(w["bottom"])) / 2.0,
                })
                break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 가구/설비 필터 (방으로 안 잡힌 가구 footprint 를 방 폴리곤에 병합)
# ─────────────────────────────────────────────────────────────────────────────

def _merge_furniture_into_rooms(outline_mm: List[Tuple[float, float]],
                                rooms: List[dict]) -> int:
    """
    방으로 안 잡혀 3D에서 솔리드 블록으로 남는 가구/설비(식탁·싱크대·조리대·
    신발장·욕조·변기)를, 그것을 둘러싼 방 폴리곤에 합쳐 없앤다. rooms 를 in-place
    수정하고 병합한 덩어리 수를 반환.

    원리: 3D 벽 = 외곽 − 방(구멍) 압출. 즉 (건물내부 − 방폴리곤)이 솔리드로 남는데
    이는 '구조벽 + 가구 footprint'다.
      · 구조벽은 가늘고 길다(선형) → opening(침식→팽창)에서 사라진다.
      · 가구/설비는 2D 덩어리 → opening에서 살아남는다(방 폴리곤이 노치로 배제하므로
        윤곽선만 그린 가구도 footprint 전체가 솔리드로 잡힘).
    살아남은 덩어리를 인접 방 폴리곤에 union 하면 그 방이 footprint를 덮어 3D
    블록이 사라진다. 가는 벽은 안 건드리므로 방이 합쳐지거나 새지 않는다(구조 보존).

    ⚠️ 한계: 두 방향 모두 _FURN_BLOB_MM↑로 굵은 구조 기둥은 가구로 오인될 수 있어
            _FURN_MAX_M2 로 큰 덩어리는 제외(놓친 방·구조 코어 보호).
    """
    if not rooms:
        return 0
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    msc = _FURN_RASTER_PXMM
    Wp = int(max(p[0] for p in outline_mm) * msc) + 2
    Hp = int(max(p[1] for p in outline_mm) * msc) + 2

    def _raster(poly):
        return np.array([[int(x * msc), int(y * msc)] for x, y in poly], np.int32)

    bmask = np.zeros((Hp, Wp), np.uint8)
    cv2.fillPoly(bmask, [_raster(outline_mm)], 255)
    rmask = np.zeros((Hp, Wp), np.uint8)
    for r in rooms:
        cv2.fillPoly(rmask, [_raster(r["polygon_mm"])], 255)

    solid = cv2.bitwise_and(bmask, cv2.bitwise_not(rmask))   # 구조벽 + 가구 footprint
    k = max(3, int(_FURN_BLOB_MM * msc))
    blobs = cv2.morphologyEx(
        solid, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    cnts, _h = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    shp = []
    for r in rooms:
        try:
            shp.append(Polygon(r["polygon_mm"]).buffer(0))
        except Exception:
            shp.append(None)

    merged = 0
    for c in cnts:
        ap = cv2.approxPolyDP(c, 2.0, True)
        pts = [(float(p[0][0]) / msc, float(p[0][1]) / msc) for p in ap]
        if len(pts) < 3:
            continue
        try:
            blk = Polygon(pts).buffer(0)
        except Exception:
            continue
        a_m2 = blk.area / 1e6
        if a_m2 < _ROOM_MIN_AREA_M2 * 0.3 or a_m2 > _FURN_MAX_M2:
            continue
        # 이 덩어리를 둘러싼(가장 많이 겹치는) 방 찾기 — 벽 두께만큼 부풀려 닿게
        probe = blk.buffer(_FURN_BLOB_MM)
        best, bi = 0.0, None
        for i, p in enumerate(shp):
            if p is None or p.is_empty:
                continue
            inter = p.intersection(probe).area
            if inter > best:
                best, bi = inter, i
        if bi is None:
            continue
        # 방 + 덩어리 union(틈은 60mm 부풀려 메움) → 구멍은 외곽만 취해 채움
        u = unary_union([shp[bi], blk.buffer(60.0)]).buffer(0)
        if u.geom_type == "MultiPolygon":
            u = max(u.geoms, key=lambda g: g.area)
        if u.geom_type == "Polygon":
            shp[bi] = Polygon(u.exterior)  # 내부 구멍 채움
            merged += 1

    for i, r in enumerate(rooms):
        p = shp[i]
        if p is None or p.is_empty or p.geom_type != "Polygon":
            continue
        p = p.simplify(20.0)
        r["polygon_mm"] = [(round(x, 1), round(y, 1)) for x, y in list(p.exterior.coords)[:-1]]
        r["area_m2"] = round(p.area / 1e6, 2)
    return merged


def _free_components_to_rooms(free: np.ndarray, sc: float, s: float,
                             in_bmask: Optional[np.ndarray] = None) -> List[dict]:
    """
    free 의 연결요소를 방으로 추출. 이미지 경계에 닿거나 _ROOM_MIN_AREA_M2 미만은
    제외. in_bmask 가 주어지면 중심이 그 건물 blob 안인 것만 채택(stroke 경로).
    반환: [{contour_pt:[(x,y)...], area_pt2}, ...] (PDF pt, y-down)
    """
    n, labels, stats, cent = cv2.connectedComponentsWithStats(free, 4)
    border = (set(labels[0, :]) | set(labels[-1, :]) |
              set(labels[:, 0]) | set(labels[:, -1]))
    px_to_m2 = 1.0 / (sc * sc) * s * s / 1e6
    eps = _ROOM_EPS_PT * sc
    rooms = []
    for i in range(1, n):
        if i in border or stats[i, cv2.CC_STAT_AREA] * px_to_m2 < _ROOM_MIN_AREA_M2:
            continue
        if in_bmask is not None:
            cx, cy = cent[i]
            if in_bmask[int(cy), int(cx)] == 0:
                continue
        comp = (labels == i).astype(np.uint8) * 255
        cnts, _h = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        approx = cv2.approxPolyDP(c, eps, True)
        if len(approx) < 3:
            continue
        rooms.append({
            "contour_pt": [(float(p[0][0]) / sc, float(p[0][1]) / sc) for p in approx],
            "area_pt2": float(stats[i, cv2.CC_STAT_AREA]) / (sc * sc),
        })
    return rooms


# ─────────────────────────────────────────────────────────────────────────────
# stroke 기반 경로 (벽을 얇은 선으로 그린 도면: 역곡동빌라.pdf)
# ─────────────────────────────────────────────────────────────────────────────

def _stroke_wall_mask(page, sc: float, shape) -> np.ndarray:
    """
    검은 구조 잉크(채움 벽 + 검은 stroke 선/curve)를 모은 벽 마스크.
    자기 bbox가 페이지의 _BIG_ELEM_FRAC↑ 단일요소(대지경계선·페이지테두리)는 제외.
    회색 치수/그리드선은 검정이 아니라 자연 제외.
    """
    W, H = float(page.width), float(page.height)
    bw, bh = W * _BIG_ELEM_FRAC, H * _BIG_ELEM_FRAC
    m = np.zeros(shape, np.uint8)

    def small(o):
        return (o["x1"] - o["x0"]) < bw and (o["bottom"] - o["top"]) < bh

    for c in page.curves:
        pts = c.get("pts") or []
        if not small(c) or len(pts) < 2:
            continue
        if c.get("fill") and _is_black_fill(c) and len(pts) >= 3:
            cv2.fillPoly(m, [np.array([[int(x * sc), int(y * sc)] for x, y in pts], np.int32)], 255)
        if c.get("stroke") and _is_black_stroke(c):
            lw = float(c.get("linewidth") or 0)
            cv2.polylines(m, [np.array([[int(x * sc), int(y * sc)] for x, y in pts], np.int32)],
                          False, 255, max(2, int(lw * sc)))
    for l in page.lines:
        if not (_is_black_stroke(l) and small(l)):
            continue
        lw = float(l.get("linewidth") or 0)
        cv2.line(m, (int(l["x0"] * sc), int(l["top"] * sc)),
                 (int(l["x1"] * sc), int(l["bottom"] * sc)), 255, max(2, int(lw * sc)))
    return m


def _stroke_building_and_rooms(page, s: float):
    """
    벽을 선으로 그린 도면에서 외곽+방 추출.
      1) 검은 벽 잉크 래스터(대지경계·테두리 제외)
      2) 자유공간(=非벽)의 연결요소 중 이미지 경계에 안 닿는 것 = 방 후보
      3) 방 후보 합집합을 크게 close → 최대 blob = 건물.
         치수선 사이 가짜 방은 이 건물 blob 밖이라 제외됨.
      4) 건물 blob contour = 외곽. blob 안의 방 후보만 채택.
    반환: (outline_pt 또는 None, [{contour_pt, area_pt2}, ...])
    """
    W, H = float(page.width), float(page.height)
    sc = _RASTER_SCALE
    shape = (int(H * sc) + 1, int(W * sc) + 1)

    wall = _stroke_wall_mask(page, sc, shape)
    seal = max(3, int(_WALL_SEAL_PT * sc) | 1)
    wall = cv2.morphologyEx(wall, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (seal, seal)))

    free = cv2.bitwise_not(wall)
    if _ROOM_OPEN_PX > 0:
        free = cv2.morphologyEx(free, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (_ROOM_OPEN_PX, _ROOM_OPEN_PX)))

    n, labels, stats, cent = cv2.connectedComponentsWithStats(free, 4)
    border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    px_to_m2 = 1.0 / (sc * sc) * s * s / 1e6
    cand = [i for i in range(1, n)
            if i not in border and stats[i, cv2.CC_STAT_AREA] * px_to_m2 >= _ROOM_MIN_AREA_M2]
    if not cand:
        return None, []

    # 방 후보 합집합 → 건물 한 덩어리로 묶기 → 최대 blob = 건물
    union = np.zeros(shape, np.uint8)
    for i in cand:
        union[labels == i] = 255
    mk = max(3, int(_BUILDING_MERGE_PT * sc) | 1)
    blob = cv2.morphologyEx(union, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (mk, mk)))
    bn, blab, bstats, _b = cv2.connectedComponentsWithStats(blob, 8)
    if bn <= 1:
        return None, []
    bbig = 1 + int(np.argmax(bstats[1:, cv2.CC_STAT_AREA]))
    bmask = (blab == bbig).astype(np.uint8) * 255

    # 외곽 = 건물 blob contour
    bcnts, _c = cv2.findContours(bmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    outline_pt = None
    if bcnts:
        main = max(bcnts, key=cv2.contourArea)
        ap = cv2.approxPolyDP(main, _OUTLINE_EPS_PT * sc, True)
        outline_pt = [(float(q[0][0]) / sc, float(q[0][1]) / sc) for q in ap]

    # 건물 blob 안의 방 채택
    rooms = _free_components_to_rooms(free, sc, s, in_bmask=bmask)
    return outline_pt, rooms


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
      rooms             : [{id, name, names:[...], polygon_mm:[(x,y)...], area_m2}, ...]
                          벽 planar subdivision으로 나눈 방. 면적 내림차순 id.
                          name=폴리곤 내부 방이름 텍스트(없으면 None). names=내부의
                          모든 방이름(문으로 더 쪼갤 때 활용).
      wall_repr         : "fill"|"stroke"  벽 표현 방식(채움 벽 / 선 벽)
      origin_mm         : [x,y]  정규화 전 좌상단(절대 mm). 렌더 위 역투영용
                          px = (mm + origin_mm) / scale_mm_per_pt × dpi/72
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

    # 방 이름 텍스트(있으면) — 두 경로 공통으로 매칭에 사용
    name_words = _room_name_words(page)

    # 건물 외곽 — 채움 벽(fills) 우선. 면적이 너무 작으면 벽이 선(stroke)으로
    # 그려진 도면이므로 stroke 기반 경로로 전환.
    outline_pt = _building_outline_pts_pt(page)
    fills_area = (_shoelace([(x * s, y * s) for x, y in outline_pt]) / 1e6) if outline_pt else 0.0

    if outline_pt and fills_area >= _MIN_BUILDING_M2:
        wall_repr = "fill"
        room_src = _extract_rooms_pt(page, outline_pt, s)
    else:
        wall_repr = "stroke"
        warnings.append("벽이 선(stroke)으로 그려진 도면 — stroke 기반 외곽/방 추출 경로 사용")
        outline_stroke, room_src = _stroke_building_and_rooms(page, s)
        if outline_stroke:
            outline_pt = outline_stroke

    if not outline_pt:
        raise ValueError("건물 외곽을 찾지 못함")

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

    # 방 구획 — 같은 정규화 적용 + 방 이름 텍스트 매칭(폴리곤 내부 점) + 면적순 id
    rooms = []
    for rm in sorted(room_src, key=lambda r: r["area_pt2"], reverse=True):
        a_m2 = rm["area_pt2"] * s * s / 1e6
        if a_m2 < _ROOM_MIN_AREA_M2:
            continue
        contour_np = np.array(rm["contour_pt"], np.float32).reshape(-1, 1, 2)
        names = [nw["text"] for nw in name_words
                 if cv2.pointPolygonTest(contour_np, (float(nw["cx"]), float(nw["cy"])), False) >= 0]
        poly = [(round(x * s - min_x, 1), round(y * s - min_y, 1))
                for x, y in rm["contour_pt"]]
        rooms.append({
            "id": len(rooms),
            "name": names[0] if names else None,   # 텍스트 라벨 없으면 None(추후 OCR)
            "names": names,                         # 한 방에 여러 이름이면 모두 기록
            "polygon_mm": poly,
            "area_m2": round(a_m2, 2),
        })

    # 가구/설비(식탁·싱크대·욕조 등) footprint 를 인접 방에 병합 → 3D 솔리드 블록 제거
    n_furn = _merge_furniture_into_rooms(outline_mm, rooms)
    if n_furn:
        warnings.append(f"가구/설비 덩어리 {n_furn}개를 방에 병합(3D 블록 제거)")

    title_text = _title_block_text(page)

    # 내부 라벨이 벡터인지 점검: 외곽 bbox 안의 실제 char 수
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
        "origin_mm": [round(min_x, 1), round(min_y, 1)],  # 정규화 전 좌상단(절대 mm)
        "walls": walls,
        "rooms": rooms,
        "wall_repr": wall_repr,
        "title_text": title_text,
        "page_size_pt": [round(float(page.width), 1), round(float(page.height), 1)],
        "warnings": warnings,
    }


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")
    path = sys.argv[1] if len(sys.argv) > 1 else "테스트용.pdf"
    page_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    r = parse_pdf(path, page_index=page_index)
    r_print = dict(r)
    r_print["building_outline_mm"] = f"{len(r['building_outline_mm'])}점"
    r_print["walls"] = f"{len(r['walls'])}개"
    r_print["title_text"] = f"{len(r['title_text'])}줄"
    r_print["rooms"] = f"{len(r['rooms'])}개"
    print(json.dumps(r_print, ensure_ascii=False, indent=2))
    print("\n방별 이름/면적:")
    for rm in r["rooms"]:
        nm = "/".join(rm["names"]) if rm["names"] else "(이름없음)"
        print(f"  room{rm['id']:>2}: {rm['area_m2']:>7.2f} m²  {nm}")
    print("\n외곽:", r["outline_area_m2"], "m²,", len(r["building_outline_mm"]), "점,",
          "wall_repr =", r["wall_repr"])
