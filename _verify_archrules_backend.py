# -*- coding: utf-8 -*-
"""건축 규칙 검증 + 방위 + 재생성 루프 — 백엔드 단위 검증 (AI 호출 없이, caller 주입).
_room_category·_touches_outer·_edge_directions·_room_facings·_validate_layout(나쁜/좋은/방위
SOFT)·_violations_feedback·_generate_with_retries(나쁨×2→좋음 멈춤 / 나쁨×3→best+ok=false)."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
from shapely.geometry import Polygon

import server as S

PASS, FAIL = [], []
def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else " FAIL ") + name)

def rect(name, x, y, w, h):
    """clipped 방 형식 {name, poly, area_m2, grid_bbox}."""
    p = Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
    return {"name": name, "poly": p, "area_m2": round(p.area / 1e6, 2),
            "grid_bbox": (x, y, x + w, y + h)}

# 합성 외곽 10m × 8m (80㎡)
BND = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]
OUT = Polygon([(x, y) for x, y in BND])

# ── 1) _room_category 우선순위 ──────────────────────────────────────────────
print("\n[1] _room_category")
ck("주방→kitchen (NOT bedroom)", S._room_category("주방") == "kitchen")
ck("안방→bedroom", S._room_category("안방") == "bedroom")
ck("침실1→bedroom", S._room_category("침실1") == "bedroom")
ck("화장실→bath", S._room_category("화장실") == "bath")
ck("욕실→bath", S._room_category("욕실") == "bath")
ck("거실→living", S._room_category("거실") == "living")
ck("LDK→living", S._room_category("LDK") == "living")
ck("다용도실→utility", S._room_category("다용도실") == "utility")
ck("발코니→balcony", S._room_category("발코니") == "balcony")
ck("현관→entry", S._room_category("현관") == "entry")

# ── 2) _touches_outer ──────────────────────────────────────────────────────
print("\n[2] _touches_outer (기준=세대 외곽)")
wall_room = rect("거실", 0, 0, 5000, 4000)["poly"]      # 상단 외벽 접
inner_room = rect("거실", 3000, 3000, 4000, 2000)["poly"]  # 가운데 갇힘
ck("외벽 붙은 방 True", S._touches_outer(wall_room, OUT) is True)
ck("가운데 갇힌 방 False", S._touches_outer(inner_room, OUT) is False)

# ── 3) _edge_directions 4방위 회전 ─────────────────────────────────────────
print("\n[3] _edge_directions")
ck("모름→None", S._edge_directions("모름") is None)
ck("북: 상북·우동·하남·좌서", S._edge_directions("북") == {"top":"북","right":"동","bottom":"남","left":"서"})
ck("남: 상남·우서·하북·좌동", S._edge_directions("남") == {"top":"남","right":"서","bottom":"북","left":"동"})
ck("동: 상동·우남·하서·좌북", S._edge_directions("동") == {"top":"동","right":"남","bottom":"서","left":"북"})
ck("서: 상서·우북·하동·좌남", S._edge_directions("서") == {"top":"서","right":"북","bottom":"동","left":"남"})

# ── 4) _room_facings ───────────────────────────────────────────────────────
print("\n[4] _room_facings (방위 남: 상단변=남)")
ed = S._edge_directions("남")
top_room = rect("거실", 0, 0, 5000, 4000)["poly"]      # 상단(=남)
bot_room = rect("거실", 0, 4000, 5000, 4000)["poly"]   # 하단(=북)
mid_room = rect("거실", 3000, 3000, 4000, 2000)["poly"]
ck("상단 방 → 남 향", "남" in S._room_facings(top_room, OUT, ed))
ck("하단 방 → 북 향", "북" in S._room_facings(bot_room, OUT, ed))
ck("가운데 방 → facings 비어있음", len(S._room_facings(mid_room, OUT, ed)) == 0)
ck("edge_dirs None → 빈 set", len(S._room_facings(top_room, OUT, None)) == 0)

# ── 5) _validate_layout: 나쁜 배치 → HARD 검출 ─────────────────────────────
print("\n[5] _validate_layout 나쁜 배치")
bad = [
    rect("거실", 3000, 3000, 4000, 3000),   # 가운데 갇힘(채광X), 12㎡/4000 ok → daylight HARD만
    rect("욕실", 0, 0, 1300, 1300),         # 1.69㎡<3 & 폭1300<1500 → HARD 2
    rect("침실1", 0, 2000, 2000, 4000),     # 폭2000<2400 → HARD (면적8 ok, 좌측 외벽 접 ok)
]
V = S._validate_layout(bad, OUT, 2, 1, None)
hard = [v for v in V if v["severity"] == "hard"]
rules = {(v["cat"], v["rule"]) for v in hard}
print("   HARD:", [v["msg"] for v in hard])
ck("거실 채광 HARD 검출", ("living", "daylight") in rules)
ck("욕실 면적 HARD 검출", ("bath", "area") in rules)
ck("욕실 폭 HARD 검출", ("bath", "width") in rules)
ck("침실 폭 HARD 검출", ("bedroom", "width") in rules)

# ── 6) _validate_layout: 좋은 배치 → HARD 0 ────────────────────────────────
print("\n[6] _validate_layout 좋은 배치")
good = [
    rect("거실", 0, 0, 5000, 4000),       # 상단 외벽, 20㎡, 한변5000
    rect("침실1", 5000, 0, 2500, 4000),   # 상단, 폭2500, 10㎡
    rect("침실2", 7500, 0, 2500, 4000),   # 상단+우측, 폭2500, 10㎡
    rect("욕실", 0, 4000, 2000, 2000),    # 4㎡, 폭2000
    rect("주방", 2000, 4000, 2000, 2000), # 폭2000(≥1800), 욕실과 인접
    rect("현관", 4000, 4000, 2000, 2000),
]
Vg = S._validate_layout(good, OUT, 2, 1, None)
hardg = [v for v in Vg if v["severity"] == "hard"]
print("   잔여 HARD:", [v["msg"] for v in hardg], " SOFT:", [v["msg"] for v in Vg if v["severity"]=="soft"])
ck("좋은 배치 HARD 0", len(hardg) == 0)

# ── 7) 방위 SOFT: 거실이 남향(상단) 미접 → 경고 ────────────────────────────
print("\n[7] 방위 SOFT (남: 상단=남)")
south_bad = [rect("거실", 0, 4000, 5000, 4000)]   # 하단(=북)에만 접, 남 미접
Vs = S._validate_layout(south_bad, OUT, 0, 0, "남")
soft_orient = [v for v in Vs if v["severity"] == "soft" and v["rule"] == "orient"]
print("   SOFT orient:", [v["msg"] for v in Vs if v["rule"]=="orient"])
ck("거실 남향 미접 → SOFT orient", any(v["cat"] == "living" for v in soft_orient))
# 비교: 모름이면 방위 검증 없음
Vs0 = S._validate_layout(south_bad, OUT, 0, 0, None)
ck("모름이면 orient 검증 없음", not any(v["rule"] == "orient" for v in Vs0))

# ── 8) _violations_feedback ────────────────────────────────────────────────
print("\n[8] _violations_feedback")
fb = S._violations_feedback(V, bad)
ck("HARD 있으면 피드백 비어있지 않음", len(fb) > 0)
ck("피드백에 방 이름 포함", "욕실" in fb and "침실1" in fb)
ck("HARD 없으면 빈 문자열", S._violations_feedback(Vg, good) == "")

# ── 9) _generate_with_retries (caller 주입) ────────────────────────────────
print("\n[9] _generate_with_retries")
BAD_JSON = json.dumps({"rooms": [
    {"name": "거실", "x": 3000, "y": 3000, "w": 4000, "h": 3000},  # 채광X
    {"name": "욕실", "x": 0, "y": 0, "w": 1300, "h": 1300},        # 규격 미달
    {"name": "침실1", "x": 0, "y": 2000, "w": 2000, "h": 4000},
]})
GOOD_JSON = json.dumps({"rooms": [
    {"name": "거실", "x": 0, "y": 0, "w": 5000, "h": 4000},
    {"name": "침실1", "x": 5000, "y": 0, "w": 2500, "h": 4000},
    {"name": "침실2", "x": 7500, "y": 0, "w": 2500, "h": 4000},
    {"name": "욕실", "x": 0, "y": 4000, "w": 2000, "h": 2000},
    {"name": "주방", "x": 2000, "y": 4000, "w": 2000, "h": 2000},
    {"name": "현관", "x": 4000, "y": 4000, "w": 2000, "h": 2000},
]})

def make_caller(scripts):
    state = {"i": 0}
    def caller(prompt):
        s = scripts[min(state["i"], len(scripts) - 1)]
        state["i"] += 1
        return s
    return caller, state

# 9a) 나쁨→나쁨→좋음: 좋음에서 멈춤(ok=True)
caller, st = make_caller([BAD_JSON, BAD_JSON, GOOD_JSON])
res = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 2, 1, None, caller, max_tries=3)
room_list, walls, warnings, ok, attempts, clipped = res
print(f"   9a 호출 {st['i']}회 attempts={attempts} ok={ok} 방={len(room_list)} warn={len(warnings)}")
ck("9a 좋음에서 멈춤(ok=True)", ok is True)
ck("9a 3회 호출(나쁨2+좋음)", st["i"] == 3)
ck("9a 방 6개", len(room_list) == 6)

# 9b) 나쁨×3: best 반환·ok=False·warnings 있음
caller2, st2 = make_caller([BAD_JSON, BAD_JSON, BAD_JSON])
res2 = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 2, 1, None, caller2, max_tries=3)
room_list2, walls2, warnings2, ok2, attempts2, _ = res2
print(f"   9b 호출 {st2['i']}회 attempts={attempts2} ok={ok2} warn={len(warnings2)}")
ck("9b 3회 시도", attempts2 == 3 and st2["i"] == 3)
ck("9b ok=False (HARD 잔존)", ok2 is False)
ck("9b warnings 비어있지 않음", len(warnings2) > 0)
ck("9b best라도 방/벽 반환", len(room_list2) > 0 and len(walls2) > 0)

# 9c) 첫 시도부터 좋음: 1회 호출로 멈춤
caller3, st3 = make_caller([GOOD_JSON])
res3 = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 2, 1, None, caller3, max_tries=3)
ck("9c 첫 시도 좋음 → 1회 호출", st3["i"] == 1 and res3[3] is True)

# 9d) 전부 파싱 실패 → None
caller4, _ = make_caller(["산문 텍스트 no json", "여전히 없음", "또 없음"])
res4 = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 2, 1, None, caller4, max_tries=3)
ck("9d 전부 파싱실패 → None (422 조건)", res4 is None)

print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL GREEN")
