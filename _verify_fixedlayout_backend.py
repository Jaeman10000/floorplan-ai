# -*- coding: utf-8 -*-
"""고정 방 연결(generate-layout) 백엔드 단위 검증 — AI 호출 없이 후처리/avail 로직만.
검증: avail=difference 정확성(고정방 2개·MultiPolygon)·clip_poly로 고정 영역 겹치는
rect/벽 잘림·avail 빈 경우 판정·프롬프트가 고정 이름을 공용공간에서 제외."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
from shapely.geometry import Polygon
from shapely.ops import unary_union

import server as S

PASS, FAIL = [], []
def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else " FAIL ") + name)

# 10m x 8m 외곽 (mm)
boundary = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]
bpoly = Polygon([(x, y) for x, y in boundary])

# ── 1) avail = difference (고정방 2개) ──────────────────────────────────────
print("\n[1] avail = 외곽 − ∪(고정방)")
fixed = [
    {"name": "현관", "poly": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]},      # 4㎡
    {"name": "다용도실", "poly": [[8000, 6000], [10000, 6000], [10000, 8000], [8000, 8000]]},  # 4㎡
]
fpolys = [Polygon([(p[0], p[1]) for p in fr["poly"]]) for fr in fixed]
fixed_union = unary_union(fpolys)
avail = bpoly.difference(fixed_union).buffer(0)
ck("avail 면적 = 외곽80 − 고정8 = 72㎡", abs(avail.area / 1e6 - 72.0) < 0.01)
ck("avail 비어있지 않음", not avail.is_empty)
# 고정 영역의 점은 avail 밖
from shapely.geometry import Point
ck("현관 중심(1000,1000)은 avail 밖", not avail.contains(Point(1000, 1000)))
ck("빈 공간 중심(5000,4000)은 avail 안", avail.contains(Point(5000, 4000)))

# ── 2) avail이 MultiPolygon이어도 intersection 동작 ──────────────────────────
print("\n[2] MultiPolygon avail clip")
# 가운데 세로 띠를 고정 → 좌/우 두 조각 = MultiPolygon
mid = {"name": "코어", "poly": [[4500, 0], [5500, 0], [5500, 8000], [4500, 8000]]}
midp = Polygon([(p[0], p[1]) for p in mid["poly"]])
avail2 = bpoly.difference(midp).buffer(0)
ck("avail2 가 MultiPolygon", avail2.geom_type == "MultiPolygon")
# 가운데를 가로지르는 rect는 clip하면 두 조각, _rects는 큰 조각만 남김
rects = [{"name": "거실", "x": 0, "y": 0, "w": 10000, "h": 4000}]
rl, walls = S._rects_to_rooms_and_walls(rects, boundary, clip_poly=avail2)
ck("MultiPolygon clip 후에도 방 생존", len(rl) >= 1)
# 생존 방의 중심은 avail2 안 (고정 코어 밖)
if rl:
    cx, cy = rl[0]["cx"], rl[0]["cy"]
    ck("생존 방 중심이 고정 코어(4500~5500) 밖", not (4500 <= cx <= 5500))

# ── 3) clip_poly로 고정 영역 겹치는 rect 잘림 ───────────────────────────────
print("\n[3] 고정 영역 침범 rect 클립")
# 현관(0~2000,0~2000)을 덮는 큰 rect → avail로 클립되면 현관 부분 제거
rects3 = [{"name": "침범방", "x": 0, "y": 0, "w": 4000, "h": 4000}]  # 16㎡, 현관 4㎡ 겹침
rl3, walls3 = S._rects_to_rooms_and_walls(rects3, boundary, clip_poly=avail)
ck("침범 rect 생존(클립됨)", len(rl3) == 1)
if rl3:
    ck("클립 후 면적 < 원래 16㎡ (현관 잘림)", rl3[0]["area_m2"] < 16.0 - 1.0)
    ck("클립 후 면적 ≈ 12㎡", abs(rl3[0]["area_m2"] - 12.0) < 0.5)
    # 중심이 현관 영역 밖
    cx, cy = rl3[0]["cx"], rl3[0]["cy"]
    ck("클립 방 중심이 현관(0~2000) 밖", not (cx < 2000 and cy < 2000))

# 비교: clip_poly 없이(외곽 전체)면 면적 16 전부
rl3b, _ = S._rects_to_rooms_and_walls(rects3, boundary, clip_poly=None)
ck("clip_poly=None이면 면적 16 전부 (기존 동작 보존)", rl3b and abs(rl3b[0]["area_m2"] - 16.0) < 0.5)

# ── 4) 벽도 avail로 클립 (고정 영역 안 벽 제거) ──────────────────────────────
print("\n[4] 벽 clip_poly")
# 현관 한가운데를 지나는 벽 → avail buffer(50) 클립이면 현관 내부 구간 제거
seg = [{"a": [1000, 0], "b": [1000, 8000]}]  # x=1000 세로선, 현관(x<2000) 안 일부
wclip = S._postprocess_walls(seg, boundary, clip_poly=avail)
# 현관 내부 (y<2000) 구간은 잘리고 y>2000 구간만 남아야
inside_fixed = any(min(w["a"][1], w["b"][1]) < 1900 and max(w["a"][1], w["b"][1]) < 2100 for w in wclip)
# 더 직접적으로: 남은 선분 중 완전히 y<2000 (현관 안) 인 게 없어야
all_in_fixed = [w for w in wclip if max(w["a"][1], w["b"][1]) <= 2000]
ck("현관 내부(y<2000) 벽 구간 제거됨", len(all_in_fixed) == 0)
ck("현관 밖 벽 구간은 남음", any(min(w["a"][1], w["b"][1]) >= 2000 for w in wclip))

# ── 5) avail 빈/작은 경우 판정 (엔드포인트 로직과 동일 임계 2㎡) ──────────────
print("\n[5] avail 너무 작음 판정")
full_cover = [{"name": "전체", "poly": [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]}]
fcp = unary_union([Polygon([(p[0], p[1]) for p in full_cover[0]["poly"]])])
avail_empty = bpoly.difference(fcp).buffer(0)
ck("외곽 전체 고정 시 avail 면적 < 2㎡ (422 조건)", avail_empty.is_empty or avail_empty.area < 2e6)

# ── 6) 프롬프트: 고정 이름이 공용공간 todo에서 제외 ─────────────────────────
print("\n[6] 프롬프트 고정 이름 제외")
bbox = (0, 0, 10000, 8000)
p_no = S._build_layout_prompt(boundary, bbox, 80.0, "A", 2, 1)
ck("고정 없을 때 외곽 폴리곤 좌표 포함", "외곽 폴리곤 좌표" in p_no)
ck("고정 없을 때 현관 공용공간에 포함", "현관" in p_no)

p_fix = S._build_layout_prompt(boundary, bbox, 80.0, "A", 2, 1,
                               fixed_rooms=[{"name": "현관", "poly": fixed[0]["poly"]}],
                               avail_bbox=(0, 0, 10000, 8000))
# 새 프롬프트: "배치 가능 전체 영역 bbox" + "이미 점유된 구역" + 비겹침 조건 형식
ck("고정 시 '배치 가능 전체 영역 bbox' 포함", "배치 가능 전체 영역 bbox" in p_fix)
ck("고정 시 '이미 점유된 구역' 섹션 포함", "이미 점유된 구역" in p_fix)
ck("고정 시 격자 스냅 비겹침 조건 포함", "x+w<=" in p_fix or "x>=" in p_fix)
# 새 프롬프트: 공용공간 목록(public_str)에 현관이 없어야 하고, [요청]에는 "이미 배치됐으니 다시 만들지 마라" 형태로만 언급
req_part = p_fix.split("[요청]")[1] if "[요청]" in p_fix else ""
ck("공용공간 목록(공용공간(...))에서 현관 제외", "현관" not in (req_part.split("공용공간(")[1].split(")")[0] if "공용공간(" in req_part else ""))
ck("요청줄에 '다시 만들지 마라' 포함 (재생성 금지 명시)", "다시 만들지 마라" in req_part)
ck("고정 시에도 거실은 만들 공용공간에 남음", "거실" in p_fix.split("[요청]")[1])

print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL GREEN")
