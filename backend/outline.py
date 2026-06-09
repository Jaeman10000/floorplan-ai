# outline.py
# 외곽 좌표 관리 — 절대 변경 불가
# MD 규칙: "외곽을 1mm라도 건드리면 전체 설계 무효"

from shapely.geometry import Polygon
from typing import List, Tuple
import math

# ─────────────────────────────────────────────────────────────────
# A세대 외곽 (원본 이미지 픽셀 좌표 → mm 변환)
# 원본 px: [(16,23),(525,22),(526,186),(437,188),(436,306),
#           (332,311),(331,450),(260,450),(259,382),(17,383)]
# 1px = 18.95mm (전용면적 58.49m² 역산)
# ─────────────────────────────────────────────────────────────────

SCALE_MM_PER_PX = 18.95

# 원본 픽셀 좌표 (절대 수정 금지)
_A_UNIT_PX: List[Tuple[int, int]] = [
    (16,  23),
    (525, 22),
    (526, 186),
    (437, 188),
    (436, 306),
    (332, 311),
    (331, 450),
    (260, 450),
    (259, 382),
    (17,  383),
]

def px_to_mm(pts_px: List[Tuple[int, int]],
             scale: float = SCALE_MM_PER_PX) -> List[Tuple[float, float]]:
    """픽셀 좌표 → mm 좌표 변환"""
    return [(x * scale, y * scale) for x, y in pts_px]


class UnitOutline:
    """
    세대 외곽 관리 클래스.
    생성 후 좌표를 변경할 수 없도록 불변(immutable) 구조.
    """

    def __init__(self,
                 name: str,
                 pts_px: List[Tuple[int, int]],
                 area_m2: float,
                 scale_mm_per_px: float = SCALE_MM_PER_PX):
        # __setattr__ 우회해서 초기화 (object의 __setattr__ 직접 호출)
        object.__setattr__(self, '_name',    name)
        object.__setattr__(self, '_pts_px',  tuple(pts_px))
        object.__setattr__(self, '_scale',   scale_mm_per_px)
        object.__setattr__(self, '_area_m2', area_m2)
        pts_mm = tuple(px_to_mm(list(pts_px), scale_mm_per_px))
        object.__setattr__(self, '_pts_mm',  pts_mm)
        object.__setattr__(self, '_polygon', Polygon(pts_mm))

    # ── 읽기 전용 속성 ──
    @property
    def name(self) -> str:
        return self._name

    @property
    def pts_px(self) -> tuple:
        return self._pts_px

    @property
    def pts_mm(self) -> tuple:
        return self._pts_mm

    @property
    def polygon(self) -> Polygon:
        return self._polygon

    @property
    def area_m2(self) -> float:
        return self._area_m2

    @property
    def scale(self) -> float:
        return self._scale

    # ── 검증 메서드 ──
    def contains_polygon(self, other: Polygon) -> bool:
        """다른 폴리곤이 외곽 안에 완전히 포함되는지 확인"""
        return self._polygon.contains(other)

    def contains_point(self, x_mm: float, y_mm: float) -> bool:
        """점이 외곽 안에 있는지 확인"""
        from shapely.geometry import Point
        return self._polygon.contains(Point(x_mm, y_mm))

    def usable_area_m2(self, wall_area_m2: float = 11.0) -> float:
        """실 사용 가능 공간 = 전용면적 - 벽체"""
        return self._area_m2 - wall_area_m2

    def summary(self) -> str:
        bounds = self._polygon.bounds  # (minx, miny, maxx, maxy)
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        return (
            f"[{self._name}] "
            f"전용 {self._area_m2}m² | "
            f"외곽 {w:.0f}×{h:.0f}mm | "
            f"실사용 {self.usable_area_m2():.1f}m²"
        )

    def __setattr__(self, name, value):
        """pts_px, pts_mm, polygon은 생성 후 변경 불가"""
        protected = ('_pts_px', '_pts_mm', '_polygon', '_scale', '_area_m2')
        if hasattr(self, '_pts_px') and name in protected:
            raise AttributeError(
                f"[외곽 변경 금지] '{name}'은 생성 후 수정할 수 없습니다. "
                "MD 규칙: 외곽을 1mm라도 건드리면 전체 설계 무효."
            )
        object.__setattr__(self, name, value)


# ─────────────────────────────────────────────────────────────────
# 등록된 세대 외곽 (추가 시 여기에만 작성)
# ─────────────────────────────────────────────────────────────────

A_UNIT = UnitOutline(
    name="A세대",
    pts_px=_A_UNIT_PX,
    area_m2=58.49,
)

# B, C세대는 외곽 추출 완료 후 추가 예정
# B_UNIT = UnitOutline(name="B세대", pts_px=_B_UNIT_PX, area_m2=70.19)
# C_UNIT = UnitOutline(name="C세대", pts_px=_C_UNIT_PX, area_m2=64.69)


# ─────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────

def shoelace_area_m2(pts_mm: List[Tuple[float, float]]) -> float:
    """신발끈 공식으로 폴리곤 면적(m²) 계산"""
    n = len(pts_mm)
    area = 0.0
    for i in range(n):
        x1, y1 = pts_mm[i]
        x2, y2 = pts_mm[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2 / 1e6  # mm² → m²
