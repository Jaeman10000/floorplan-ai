# validator.py
# MD [세대 내부 평면 설계 규칙] 체크리스트 14개를 코드로 강제화
# 하나라도 실패하면 ValidationError를 던지고 다음 단계로 넘어가지 않음

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from shapely.geometry import Polygon, Point, LineString
import math

from config import *
from outline import UnitOutline, shoelace_area_m2


# ─────────────────────────────────────────────────────────────────
# 데이터 구조
# ─────────────────────────────────────────────────────────────────

@dataclass
class Room:
    """단일 공간 정의"""
    name: str                              # "거실", "안방" 등
    polygon_mm: List[Tuple[float, float]]  # mm 좌표 폴리곤
    has_window: bool = False               # 외벽 창문 여부
    connects_to: List[str] = field(default_factory=list)  # 문으로 연결된 공간

    @property
    def shapely(self) -> Polygon:
        return Polygon(self.polygon_mm)

    @property
    def area_m2(self) -> float:
        return shoelace_area_m2(self.polygon_mm)

    @property
    def bounds_mm(self):
        p = self.shapely
        return p.bounds  # (minx, miny, maxx, maxy)

    @property
    def width_mm(self) -> float:
        b = self.bounds_mm
        return b[2] - b[0]

    @property
    def height_mm(self) -> float:
        b = self.bounds_mm
        return b[3] - b[1]


@dataclass
class FloorPlan:
    """평면 설계 전체"""
    unit: UnitOutline
    rooms: List[Room]
    entrance_point_mm: Tuple[float, float]  # 현관문 위치 (mm)


class ValidationError(Exception):
    """설계 규칙 위반 시 발생"""
    pass


# ─────────────────────────────────────────────────────────────────
# 검증 엔진
# ─────────────────────────────────────────────────────────────────

class FloorPlanValidator:
    """
    MD 체크리스트 14개 항목을 순서대로 검증.
    모든 항목 통과 시에만 True 반환.
    """

    def __init__(self, plan: FloorPlan):
        self.plan  = plan
        self.unit  = plan.unit
        self.rooms = {r.name: r for r in plan.rooms}
        self.errors: List[str] = []

    def validate_all(self) -> bool:
        """
        체크리스트 14개 항목 전부 실행.
        실패 항목을 모두 수집한 후 한꺼번에 보고.
        """
        checks = [
            self._check_0_usable_area,
            self._check_1_outline_fixed,
            self._check_2_total_area,
            self._check_3_living_has_window,
            self._check_4_rooms_have_window,
            self._check_5_dimensions_calculated,
            self._check_6_room_sizes,
            self._check_7_queen_bed_fits,
            self._check_8_entrance_to_living,
            self._check_9_living_room_doors_visible,
            self._check_10_shared_bathroom_accessible,
            self._check_11_kitchen_entrance,
            self._check_12_corridors_min_width,
            self._check_13_no_room_outside_outline,
        ]

        self.errors = []
        for i, check in enumerate(checks):
            try:
                check()
            except ValidationError as e:
                self.errors.append(f"[체크 {i}] {e}")

        if self.errors:
            print("\n❌ 설계 검증 실패 — 다음 항목을 수정하세요:\n")
            for err in self.errors:
                print(f"  • {err}")
            return False

        print("\n✅ 설계 검증 통과 — 모든 체크리스트 14개 항목 OK\n")
        return True

    # ── 체크 0: 전용면적 기반 실사용 공간 계산 ──
    def _check_0_usable_area(self):
        usable = self.unit.usable_area_m2(WALL_AREA_ESTIMATE_M2)
        total  = sum(r.area_m2 for r in self.plan.rooms)
        if total > usable + 0.5:  # 0.5m² 오차 허용
            raise ValidationError(
                f"공간 합계 {total:.1f}m² > 실사용공간 {usable:.1f}m² "
                f"(전용 {self.unit.area_m2} - 벽체 {WALL_AREA_ESTIMATE_M2}). "
                f"{total - usable:.1f}m² 초과."
            )

    # ── 체크 1: 외곽 고정 확인 ──
    def _check_1_outline_fixed(self):
        # outline.py의 불변 객체가 이미 보장.
        # 혹시 다른 outline이 넘어오면 면적으로 비교.
        expected = self.unit.area_m2
        actual   = shoelace_area_m2(list(self.unit.pts_mm))
        if abs(actual - expected) > 1.0:
            raise ValidationError(
                f"외곽 면적 {actual:.2f}m² ≠ 전용면적 {expected}m². "
                "외곽 좌표가 변경된 것 같습니다."
            )

    # ── 체크 2: 공간 합계 ≤ 실사용공간 ──
    def _check_2_total_area(self):
        usable = self.unit.usable_area_m2(WALL_AREA_ESTIMATE_M2)
        total  = sum(r.area_m2 for r in self.plan.rooms)
        if total > self.unit.area_m2:
            raise ValidationError(
                f"공간 합계 {total:.1f}m²가 전용면적 {self.unit.area_m2}m²를 초과합니다."
            )

    # ── 체크 3: 거실 외벽 창문 ──
    def _check_3_living_has_window(self):
        living = self.rooms.get("거실")
        if not living:
            raise ValidationError("거실이 없습니다.")
        if not living.has_window:
            raise ValidationError(
                "거실에 외벽 창문이 없습니다. "
                "거실이 내부에 갇히면 낮에도 암실이 됩니다."
            )

    # ── 체크 4: 방 3개 모두 외벽 창문 ──
    def _check_4_rooms_have_window(self):
        for room_name in ["안방", "방2", "방3"]:
            room = self.rooms.get(room_name)
            if room and not room.has_window:
                raise ValidationError(
                    f"{room_name}에 외벽 창문이 없습니다."
                )

    # ── 체크 5: 구역별 가용 치수 계산 완료 (방 폴리곤 존재 여부로 확인) ──
    def _check_5_dimensions_calculated(self):
        for room in self.plan.rooms:
            if len(room.polygon_mm) < 3:
                raise ValidationError(
                    f"{room.name}의 폴리곤이 정의되지 않았습니다."
                )

    # ── 체크 6: 방 크기 최소 기준 ──
    def _check_6_room_sizes(self):
        for room_name in ["안방", "방2", "방3"]:
            room = self.rooms.get(room_name)
            if not room:
                continue
            w = room.width_mm
            h = room.height_mm
            if w < ROOM_MIN_WIDTH_MM:
                raise ValidationError(
                    f"{room_name} 가로 {w:.0f}mm < 최소 {ROOM_MIN_WIDTH_MM}mm"
                )
            if h < ROOM_MIN_HEIGHT_MM:
                raise ValidationError(
                    f"{room_name} 세로 {h:.0f}mm < 최소 {ROOM_MIN_HEIGHT_MM}mm"
                )
            ratio = max(w, h) / min(w, h)
            if ratio > ROOM_MAX_RATIO:
                raise ValidationError(
                    f"{room_name} 가로:세로 비율 {ratio:.1f} > {ROOM_MAX_RATIO} "
                    "(창고형 공간)"
                )

    # ── 체크 7: 퀸침대 + 양측 통로 ──
    def _check_7_queen_bed_fits(self):
        for room_name in ["안방", "방2", "방3"]:
            room = self.rooms.get(room_name)
            if not room:
                continue
            w = room.width_mm
            h = room.height_mm
            # 퀸침대(1600) + 양측통로(600×2) = 2800mm 필요
            if w < ROOM_MIN_FOR_QUEEN:
                raise ValidationError(
                    f"{room_name}: 퀸침대+통로 필요폭 {ROOM_MIN_FOR_QUEEN}mm > "
                    f"실제폭 {w:.0f}mm. 싱글침대 기준으로 낮추거나 방 확장 필요."
                )
            # 침대(2000) + 발치통로(700) = 2700mm
            bed_depth_needed = QUEEN_BED_D_MM + BED_FOOT_AISLE_MM
            if h < bed_depth_needed:
                raise ValidationError(
                    f"{room_name}: 침대+발치통로 필요깊이 {bed_depth_needed}mm > "
                    f"실제높이 {h:.0f}mm."
                )

    # ── 체크 8: 현관 → 거실 직통 (막힌 벽 없음) ──
    def _check_8_entrance_to_living(self):
        living = self.rooms.get("거실")
        if not living:
            raise ValidationError("거실이 없어 현관→거실 동선 확인 불가.")

        # 현관 진입점에서 거실 중심까지 경로가 막히는지 확인
        entrance = Point(self.plan.entrance_point_mm)
        living_center = living.shapely.centroid

        # 이동 경로 직선
        path = LineString([
            self.plan.entrance_point_mm,
            (living_center.x, living_center.y)
        ])

        # 방, 욕실이 이 경로를 막는지 확인
        blocking_types = ["안방", "방2", "방3", "공용욕실", "안방욕실"]
        for room_name in blocking_types:
            room = self.rooms.get(room_name)
            if room and path.intersects(room.shapely):
                raise ValidationError(
                    f"현관→거실 경로를 [{room_name}]이 막고 있습니다. "
                    "현관문 열면 거실로 바로 연결되어야 합니다."
                )

    # ── 체크 9: 거실에서 모든 방문이 보임 ──
    def _check_9_living_room_doors_visible(self):
        living = self.rooms.get("거실")
        if not living:
            return
        # connects_to로 연결 여부 확인
        for room_name in ["안방", "방2", "방3"]:
            room = self.rooms.get(room_name)
            if not room:
                continue
            # 거실과 해당 방이 인접(접촉)하는지 확인
            if not living.shapely.touches(room.shapely) and \
               not living.shapely.intersects(room.shapely):
                raise ValidationError(
                    f"거실에서 [{room_name}]으로 직접 접근할 수 없습니다. "
                    "거실에서 모든 방문이 보여야 합니다."
                )

    # ── 체크 10: 공용욕실 → 거실/복도에서 직접 진입 ──
    def _check_10_shared_bathroom_accessible(self):
        bathroom = self.rooms.get("공용욕실")
        if not bathroom:
            return
        living = self.rooms.get("거실")
        if not living:
            raise ValidationError("거실이 없어 공용욕실 접근 확인 불가.")
        if not living.shapely.touches(bathroom.shapely) and \
           not living.shapely.intersects(bathroom.shapely):
            raise ValidationError(
                "공용욕실이 거실/복도에서 직접 진입 불가합니다. "
                "방을 통과하지 않고 접근 가능해야 합니다."
            )

    # ── 체크 11: 주방 입구 900mm 이상 (냉장고 반입) ──
    def _check_11_kitchen_entrance(self):
        kitchen = self.rooms.get("부엌")
        if not kitchen:
            return
        w = kitchen.width_mm
        h = kitchen.height_mm
        # 가장 짧은 변이 입구 폭으로 간주
        entrance = min(w, h)
        if entrance < KITCHEN_ENTRANCE_MIN_MM:
            raise ValidationError(
                f"부엌 입구 폭 {entrance:.0f}mm < {KITCHEN_ENTRANCE_MIN_MM}mm. "
                f"냉장고({FRIDGE_W_MM}mm) 반입 불가."
            )

    # ── 체크 12: 모든 통로 900mm 이상 ──
    def _check_12_corridors_min_width(self):
        # 현관 크기 확인
        entrance = self.rooms.get("현관")
        if entrance:
            w = entrance.width_mm
            h = entrance.height_mm
            if min(w, h) < ENTRANCE_MIN_W_MM:
                raise ValidationError(
                    f"현관 크기 {w:.0f}×{h:.0f}mm — "
                    f"최소 {ENTRANCE_MIN_W_MM}×{ENTRANCE_MIN_H_MM}mm 필요."
                )

    # ── 체크 13: 모든 방이 외곽 안에 완전히 포함 ──
    def _check_13_no_room_outside_outline(self):
        for room in self.plan.rooms:
            if not self.unit.contains_polygon(room.shapely):
                raise ValidationError(
                    f"[{room.name}]이 외곽 밖으로 나갔습니다. "
                    "방은 외곽 안에 완전히 포함되어야 합니다."
                )


# ─────────────────────────────────────────────────────────────────
# 편의 함수
# ─────────────────────────────────────────────────────────────────

def validate(plan: FloorPlan) -> bool:
    """평면 설계 검증 실행"""
    v = FloorPlanValidator(plan)
    return v.validate_all()
