# config.py
# MD 파일 [세대 내부 평면 설계 규칙]의 모든 수치 기준값
# 이 값들을 바꾸지 않는 한 규칙은 항상 동일하게 적용됨

# ─────────────────────────────────────
# 벽체 두께 (mm)
# ─────────────────────────────────────
OUTER_WALL_MM   = 200   # 외벽
INNER_WALL_MM   = 100   # 칸막이벽

# ─────────────────────────────────────
# 방 최소 기준 (mm / m²)
# ─────────────────────────────────────
ROOM_MIN_WIDTH_MM   = 2700
ROOM_MIN_HEIGHT_MM  = 2700
ROOM_MIN_AREA_M2    = 7.3
ROOM_MAX_RATIO      = 1.8   # 가로:세로 = 1:1.8 이내

# 퀸침대 기준 통로
QUEEN_BED_W_MM      = 1600
QUEEN_BED_D_MM      = 2000
BED_SIDE_AISLE_MM   = 600   # 침대 양옆 통로
BED_FOOT_AISLE_MM   = 700   # 침대 발치 통로
ROOM_MIN_FOR_QUEEN  = QUEEN_BED_W_MM + BED_SIDE_AISLE_MM * 2  # 2800mm

# ─────────────────────────────────────
# 통로 최소 폭 (mm)
# ─────────────────────────────────────
CORRIDOR_MIN_MM         = 900   # 모든 통로 최소
ENTRANCE_TO_LIVING_MM   = 900   # 현관→거실 최소
BATHROOM_DOOR_MM        = 800   # 욕실 문 앞 통로

# ─────────────────────────────────────
# 거실 최소 기준 (mm / m²)
# ─────────────────────────────────────
LIVING_MIN_WIDTH_MM  = 3600
LIVING_MIN_HEIGHT_MM = 3200
LIVING_MIN_AREA_M2   = 11.5

# ─────────────────────────────────────
# 주방 최소 기준 (mm / m²)
# ─────────────────────────────────────
KITCHEN_MIN_WIDTH_MM    = 2100
KITCHEN_MIN_HEIGHT_MM   = 2400
KITCHEN_MIN_AREA_M2     = 5.0
KITCHEN_ENTRANCE_MIN_MM = 900    # 주방 입구 (냉장고 반입)
FRIDGE_W_MM             = 700
FRIDGE_D_MM             = 750

# ─────────────────────────────────────
# 욕실 최소 기준 (mm)
# ─────────────────────────────────────
BATHROOM_SHARED_MIN_W_MM  = 1500
BATHROOM_SHARED_MIN_H_MM  = 2100
BATHROOM_MASTER_MIN_W_MM  = 1500
BATHROOM_MASTER_MIN_H_MM  = 2000

# ─────────────────────────────────────
# 현관 최소 기준 (mm)
# ─────────────────────────────────────
ENTRANCE_MIN_W_MM = 1200
ENTRANCE_MIN_H_MM = 1200

# ─────────────────────────────────────
# 면적 계산
# ─────────────────────────────────────
# 벽체가 전용면적에서 차지하는 비율 (외벽+내벽 합산 추정)
# 실측값: 외벽 둘레 약 35m × 0.2m = 7.0m² + 내벽 약 4.0m² = 11.0m²
WALL_AREA_ESTIMATE_M2 = 11.0

# 공간 타입 목록
VALID_ROOM_TYPES = [
    "안방", "방2", "방3",
    "거실", "부엌",
    "안방욕실", "공용욕실",
    "드레스룸", "다용도실", "현관"
]

# 공용생활공간 (현관→이 공간들까지 방 통과 없이 접근 가능해야 함)
PUBLIC_SPACES = ["거실", "부엌", "공용욕실"]

# 외벽 창문 필수 공간
REQUIRES_WINDOW = ["거실", "안방", "방2", "방3", "부엌"]
