# -*- coding: utf-8 -*-
"""설계 조언 '대략적 위치 존' — 백엔드 단위 검증 (AI 호출 없이 순수 함수).
_split_advice_and_zones(텍스트+펜스 분리·펜스 없을 때 prose 전체·zones 빈)·
_parse_zones_json(정상/펜스/산문혼합/깨짐/w0)·_clipped_rooms 외곽 클립.
★zones 파싱 실패해도 조언 텍스트(prose)가 보존되는 graceful 경로."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import server as S
from shapely.geometry import Polygon

PASS, FAIL = [], []
def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else " FAIL ") + name)


# ══ 1) _split_advice_and_zones ═══════════════════════════════════════════════
print("\n[1] _split_advice_and_zones")
prose_txt = "• 남쪽에 거실\n• 북쪽에 욕실"
zones_json = '{"zones":[{"name":"거실","x":0,"y":0,"w":4000,"h":3500}]}'

# 1a) ```json 펜스
full = prose_txt + "\n\n```json\n" + zones_json + "\n```"
p, j = S._split_advice_and_zones(full)
print(f"   prose={p[:30]!r} json_len={len(j)}")
ck("펜스: prose에 거실 조언 남음", "거실" in p and "남쪽" in p)
ck("펜스: prose에 JSON(zones) 안 남음", "zones" not in p and "```" not in p)
ck("펜스: json_str에 zones 포함", "zones" in j)

# 1b) 펜스 없는 산문 혼합 (조언 뒤 raw JSON)
full2 = prose_txt + "\n" + zones_json
p2, j2 = S._split_advice_and_zones(full2)
print(f"   산문혼합 prose={p2[:30]!r} json_has_zones={'zones' in j2}")
ck("산문혼합: prose 보존", "거실" in p2 and "zones" not in p2)
ck("산문혼합: json 추출", "zones" in j2)

# 1c) 펜스/JSON 전혀 없음 → prose 전체, zones 빈
p3, j3 = S._split_advice_and_zones(prose_txt)
ck("JSON 없음: prose 전체 보존", p3.strip() == prose_txt.strip())
ck("JSON 없음: json_str 빈 문자열", j3 == "")


# ══ 2) _parse_zones_json ═════════════════════════════════════════════════════
print("\n[2] _parse_zones_json")
z = S._parse_zones_json(zones_json)
ck("정상: 1개 파싱", len(z) == 1 and z[0]["name"] == "거실" and z[0]["w"] == 4000)

z_fence = S._parse_zones_json("```json\n" + zones_json + "\n```")
ck("펜스 제거 후 파싱", len(z_fence) == 1)

z_prose = S._parse_zones_json("여기 존입니다: " + zones_json + " 끝")
ck("산문 혼합에서 {…} 추출", len(z_prose) == 1)

z_broken = S._parse_zones_json('{"zones":[{"name":"거실",,,]')
ck("깨진 JSON → [] (예외 없음)", z_broken == [])

z_w0 = S._parse_zones_json('{"zones":[{"name":"A","x":0,"y":0,"w":0,"h":100},{"name":"B","x":0,"y":0,"w":2000,"h":2000}]}')
ck("w<=0 방 제외, 정상 방만", len(z_w0) == 1 and z_w0[0]["name"] == "B")

z_empty = S._parse_zones_json("조언만 있고 JSON 없음")
ck("JSON 아님 → []", z_empty == [])


# ══ 3) _clipped_rooms 외곽 클립 ══════════════════════════════════════════════
print("\n[3] _clipped_rooms 클립 (존을 외곽 안으로)")
bpoly = Polygon([(0, 0), (10000, 0), (10000, 8000), (0, 8000)])  # 10×8m

# 3a) 외곽 안 정상 존
inside = [{"name": "거실", "x": 1000, "y": 1000, "w": 4000, "h": 3000}]
ci = S._clipped_rooms(inside, bpoly)
ck("외곽 안 존 생존", len(ci) == 1 and ci[0]["area_m2"] > 0)
from shapely.geometry import Point
ring = list(ci[0]["poly"].exterior.coords)
ck("생존 존 정점 전부 외곽 안", all(bpoly.buffer(1.0).covers(Point(x, y)) for x, y in ring))

# 3b) 외곽 밖으로 삐져나간 존 → 안쪽만 남고 밖은 잘림
spill = [{"name": "침실", "x": 8000, "y": 6000, "w": 5000, "h": 5000}]  # 우하단서 밖으로
cs = S._clipped_rooms(spill, bpoly)
print(f"   삐짐 존 생존={len(cs)} area={cs[0]['area_m2'] if cs else 0}")
ck("삐짐 존: 생존(잘려서)", len(cs) == 1)
ck("삐짐 존: 면적 < 원래 25㎡ (밖 잘림)", cs and cs[0]["area_m2"] < 25)
if cs:
    sring = list(cs[0]["poly"].exterior.coords)
    ck("삐짐 존 클립 결과 외곽 안", all(bpoly.buffer(1.0).covers(Point(x, y)) for x, y in sring))

# 3c) <1㎡ 존 drop
tiny = [{"name": "틈", "x": 100, "y": 100, "w": 500, "h": 500}]  # 0.25㎡
ct = S._clipped_rooms(tiny, bpoly)
ck("<1㎡ 존 drop", ct == [])

# 3d) 완전히 외곽 밖 → drop
out = [{"name": "밖", "x": 20000, "y": 20000, "w": 3000, "h": 3000}]
co = S._clipped_rooms(out, bpoly)
ck("완전 외곽 밖 존 drop", co == [])


# ══ 4) ★ graceful: zones 깨져도 조언 텍스트 보존되는 전체 경로 ═══════════════
print("\n[4] ★ zones 파싱 실패해도 조언(prose) 보존")
# design_advice 엔드포인트의 zones 추출 블록과 동일한 흐름을 순수 함수로 재현
def extract(raw, boundary):
    answer, zones = raw, []
    try:
        prose, json_str = S._split_advice_and_zones(raw)
        answer = prose or raw
        if json_str:
            bp = Polygon([(float(x), float(y)) for x, y in boundary])
            if not bp.is_valid:
                bp = bp.buffer(0)
            for c in S._clipped_rooms(S._parse_zones_json(json_str), bp):
                zones.append({"name": c["name"], "poly": list(c["poly"].exterior.coords)})
    except Exception:
        zones = []
    return answer, zones

bnd = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]

# 4a) 정상
a, zz = extract(prose_txt + "\n```json\n" + zones_json + "\n```", bnd)
ck("정상: 조언 보존 + 존 추출", "거실" in a and "zones" not in a and len(zz) == 1)

# 4b) zones JSON 깨짐 → 조언은 살고 zones=[]
broken_full = prose_txt + '\n```json\n{"zones":[{"name":"거실",,, broken ```'
a2, zz2 = extract(broken_full, bnd)
print(f"   깨짐: answer에 조언={'거실' in a2 or '남쪽' in a2} zones={len(zz2)}")
ck("깨진 zones: 조언 텍스트 보존", "남쪽" in a2)
ck("깨진 zones: zones=[] (존만 안 뜸)", zz2 == [])

# 4c) zones 아예 없음 → 조언 전체 보존
a3, zz3 = extract(prose_txt, bnd)
ck("zones 없음: 조언 전체 보존", a3.strip() == prose_txt.strip() and zz3 == [])


print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL GREEN")
