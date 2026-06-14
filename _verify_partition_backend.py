# -*- coding: utf-8 -*-
"""A-2 정형 영역 규격 자동 분할 — 백엔드 단위 검증 (AI/키 불필요, 순수 함수 + 엔드포인트).
_partition_bsp(48㎡/70㎡ 위반0·빈틈없음·<200ms)·_partition_feasible 거부 게이트·
_assign_roles_by_facing 방위 배정·잔여 면적·다운스트림(BSP칸→walls·클립·dedup)·
partition-layout 엔드포인트(정형 성공·작은면적 거부·잔여 zones·고정방 차감)."""
import sys, os, time, asyncio
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import server as S
from shapely.geometry import Polygon

PASS, FAIL = [], []
def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else " FAIL ") + name)


def viol(cells):
    return sum(S._cell_penalty(c["cat"], c["w"], c["h"]) for c in cells)


def gapless(cells, rect):
    A = (rect[2]-rect[0])*(rect[3]-rect[1])
    return abs(sum(c["w"]*c["h"] for c in cells) - A) / A < 0.02


# ══ 1) _partition_bsp 규격 위반0 + 빈틈없음 + 시간 ════════════════════════════
print("\n[1] _partition_bsp")
big = (0, 0, 8000, 6000)   # 48㎡
prog = S._partition_program(2, 1)
t0 = time.perf_counter(); cells = S._partition_bsp(prog, big); dt = (time.perf_counter()-t0)*1000
v = viol(cells)
print(f"   48㎡ 침실2·욕1: 칸 {len(cells)} 위반 {v} 빈틈없음 {gapless(cells, big)} {dt:.1f}ms")
for c in cells:
    _, _, side, area = S._spec_check(c["cat"], c["w"], c["h"])
    print(f"      {c['name']:<6} {c['w']/1000:.2f}x{c['h']/1000:.2f}m={area:.1f}㎡ (변 {side/1000:.2f})")
ck("48㎡ 위반0", v == 0)
ck("48㎡ 빈틈없음", gapless(cells, big))
ck("48㎡ <200ms", dt < 200)

big2 = (0, 0, 10000, 7000)   # 70㎡
prog2 = S._partition_program(3, 2)
t0 = time.perf_counter(); cells2 = S._partition_bsp(prog2, big2); dt2 = (time.perf_counter()-t0)*1000
v2 = viol(cells2)
print(f"   70㎡ 침실3·욕2: 칸 {len(cells2)} 위반 {v2} 빈틈없음 {gapless(cells2, big2)} {dt2:.1f}ms")
ck("70㎡ 위반0", v2 == 0)
ck("70㎡ 빈틈없음", gapless(cells2, big2))
ck("70㎡ <200ms", dt2 < 200)


# ══ 2) _partition_feasible 거부 게이트 ═══════════════════════════════════════
print("\n[2] _partition_feasible (거부 게이트)")
need, have, ok = S._partition_feasible(S._partition_program(3, 1), (0, 0, 4000, 3500))  # 14㎡
print(f"   14㎡ 침실3·욕1: 필요 {need:.0f} vs {have:.1f} ok={ok}")
ck("14㎡/침실3 거부", ok is False)
need, have, ok = S._partition_feasible(S._partition_program(2, 1), (0, 0, 6000, 5000))  # 30㎡
print(f"   30㎡ 침실2·욕1: 필요 {need:.0f} vs {have:.1f} ok={ok}")
ck("30㎡/침실2 거부(필요33)", ok is False)
need, have, ok = S._partition_feasible(S._partition_program(2, 1), (0, 0, 8000, 6000))  # 48㎡
ck("48㎡/침실2 통과", ok is True)


# ══ 3) 실제 A세대 내접 — 풀프로그램 거부 / 거실만 통과 ═══════════════════════
print("\n[3] 실제 A세대 내접 (16.8㎡, ㄱ자)")
import json
bnd = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_a_boundary.json")))
bpoly = Polygon([(float(x), float(y)) for x, y in bnd]); bpoly = bpoly if bpoly.is_valid else bpoly.buffer(0)
ir = S._max_inscribed_rect(bpoly)
ia = (ir[2]-ir[0])*(ir[3]-ir[1])/1e6
print(f"   A 내접 {(ir[2]-ir[0])/1000:.2f}x{(ir[3]-ir[1])/1000:.2f}m = {ia:.1f}㎡ (외곽 {bpoly.area/1e6:.1f}㎡)")
ck("A 내접 ~16.8㎡ (외곽의 22%)", 15 < ia < 19 and ia / (bpoly.area/1e6) < 0.3)
_, _, okfull = S._partition_feasible(S._partition_program(2, 1), ir)
ck("A 내접: 침실2·욕1 풀프로그램 거부", okfull is False)
_, _, okliv = S._partition_feasible([("living", "거실", 2.2)], ir)
ck("A 내접: 거실만 통과", okliv is True)


# ══ 4) _assign_roles_by_facing 방위 배정 (남향=거실·침실) ═════════════════════
print("\n[4] _assign_roles_by_facing 방위")
# 남향(도면 위=남) 정사각형 외곽. 위쪽 변=남. 거실·침실이 남(위)쪽 칸에 가야.
sq = [[0, 0], [8000, 0], [8000, 6000], [0, 6000]]
sqp = Polygon([(x, y) for x, y in sq])
edge = S._edge_directions("남")   # top=남
prog4 = S._partition_program(2, 1)
cells4 = S._partition_bsp(prog4, (0, 0, 8000, 6000))
assigned, hard = S._assign_roles_by_facing(cells4, prog4, sqp, edge)
print(f"   배정 HARD={hard}")
for c in assigned:
    cy = c["y"] + c["h"]/2
    print(f"      {c['name']:<6} cat={c['cat']:<8} 중심y={cy:.0f} ({'위/남' if cy < 3000 else '아래/북'})")
ck("배정 HARD 0 (모두 규격·채광 충족)", hard == 0)
# 거실은 외곽 접함(daylight) 칸이어야
liv = [c for c in assigned if c["cat"] == "living"]
ck("거실 1개 배정", len(liv) == 1)
beds = [c for c in assigned if c["cat"] == "bedroom"]
ck("침실 2개 배정", len(beds) == 2)
# 물공간(욕실)은 내부/북 선호 — 적어도 거실/침실보다 남쪽일 필요 없음(소프트). 존재만 확인
baths = [c for c in assigned if c["cat"] == "bath"]
ck("욕실 1개 배정", len(baths) == 1)


# ══ 5) 다운스트림: BSP 칸 → walls (격자스냅·클립·dedup) ═══════════════════════
print("\n[5] 다운스트림 _rects_to_rooms_and_walls")
rects = [{"name": c["name"], "x": c["x"], "y": c["y"], "w": c["w"], "h": c["h"]}
         for c in assigned if c["cat"] != "other"]
room_list, walls = S._rects_to_rooms_and_walls(rects, sq, clip_poly=None)
print(f"   방 {len(room_list)} 벽 {len(walls)} (공유변 dedup으로 4×{len(rects)}보다 적어야)")
ck("방 생성됨", len(room_list) >= 4)
ck("벽 생성됨 + dedup(<4*칸수)", 0 < len(walls) < 4 * len(rects))
ck("모든 벽 좌표 유효(NaN 없음)", all(
    all(isinstance(v, (int, float)) and v == v for v in (w["a"][0], w["a"][1], w["b"][0], w["b"][1]))
    for w in walls))


# ══ 6) 엔드포인트 partition_layout ═══════════════════════════════════════════
print("\n[6] partition_layout 엔드포인트")
def call(body):
    return asyncio.run(S.partition_layout(body)).body
import json as _j
def call_json(body):
    return _j.loads(asyncio.run(S.partition_layout(body)).body)

# 6a) 정형 48㎡ 침실2·욕1 → ok
r = call_json({"boundary_mm": sq, "unit": "A", "building_orientation": "남", "rooms": 2, "baths": 1})
print(f"   정형 48㎡: ok={r['ok']} 벽 {len(r.get('walls', []))} 방 {len(r.get('rooms', []))} reason={r.get('reason','')[:40]}")
ck("정형 48㎡ ok=True", r["ok"] is True)
ck("정형 48㎡ 벽·방 생성", len(r["walls"]) > 0 and len(r["rooms"]) >= 4)
ck("정형 48㎡ 침실2·욕1 반영", r["bedrooms"] == 2 and r["baths"] == 1)

# 6b) 작은 14㎡ 침실3 → 거부
small = [[0, 0], [4000, 0], [4000, 3500], [0, 3500]]
r2 = call_json({"boundary_mm": small, "unit": "A", "rooms": 3, "baths": 1})
print(f"   작은 14㎡ 침실3: ok={r2['ok']} reason={r2.get('reason','')[:50]}")
ck("작은 14㎡ 침실3 거부(ok=False)", r2["ok"] is False)
ck("거부 시 walls 빈(기존 작업 보존)", r2["walls"] == [])
ck("거부 사유 메시지 있음", bool(r2.get("reason")))

# 6c) A세대 실제 외곽 → 거부 + 잔여 zones
rA = call_json({"boundary_mm": bnd, "unit": "A", "building_orientation": "남", "rooms": 2, "baths": 1})
print(f"   A세대: ok={rA['ok']} 잔여존 {len(rA.get('residual_zones', []))}개 reason={rA.get('reason','')[:50]}")
ck("A세대 침실2·욕1 거부(내접 too small)", rA["ok"] is False)
ck("A세대 잔여 zones 표시(가이드)", len(rA.get("residual_zones", [])) >= 1)

# 6d) 잔여 zones 폴리곤 유효 (정형은 잔여 거의 없음)
ck("정형은 잔여 거의 없음", r.get("residual_area_m2", 0) < 8)

# 6e) boundary<3 → 400
err = None
try:
    asyncio.run(S.partition_layout({"boundary_mm": [[0, 0], [1, 1]]}))
except Exception as e:
    err = e
ck("boundary<3 → 400", err is not None and getattr(err, "status_code", None) == 400)


print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL GREEN")
