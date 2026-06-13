"""
AI 구조 초안(직사각형 방식) — 백엔드 단독 검증 (API 키 불필요: 결정적 변환만).

1. _rects_to_rooms_and_walls: 격자스냅 · 외곽 정확 클립(밖 통과 금지) · area<1㎡ drop ·
   representative_point 중심(면 내부) · 4변→벽 · 인접 공유변 dedup.
2. 모든 좌표 100mm 배수 · 벽/방 전부 외곽 안 · 외곽밖 rect 통과 0 · 침실수=입력 보존.
3. _parse_rooms_json: 펜스/산문/깨짐.
4. _default_room_counts: 면적 기반 기본값.
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from server import (_rects_to_rooms_and_walls, _parse_rooms_json,
                    _default_room_counts, _build_layout_prompt)

# 10m × 8m 직사각 외곽 (mm)
BOUNDARY = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]


def on_grid(v):
    return v % 100 == 0


def walls_on_grid(walls):
    return all(on_grid(p[0]) and on_grid(p[1]) for w in walls for p in (w["a"], w["b"]))


def inside_bbox(pts, b, tol=60):
    xs = [p[0] for p in b]; ys = [p[1] for p in b]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return all(x0 - tol <= p[0] <= x1 + tol and y0 - tol <= p[1] <= y1 + tol for p in pts)


def point_in_poly(pt, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > pt[1]) != (yj > pt[1])) and (pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ── 1. 정상 배치 (침실2 + 거실/주방/욕실/현관) — 격자에서 살짝 어긋난 값 포함 ──
rects = [
    {"name": "거실",  "x": 0,    "y": 0,    "w": 5000, "h": 5000},
    {"name": "주방",  "x": 5000, "y": 0,    "w": 5000, "h": 3000},
    {"name": "침실",  "x": 5000, "y": 3017, "w": 5000, "h": 2483},   # 3017→3000 스냅
    {"name": "침실",  "x": 0,    "y": 5000, "w": 5000, "h": 3000},
    {"name": "욕실",  "x": 5000, "y": 5500, "w": 2500, "h": 2500},
    {"name": "현관",  "x": 7500, "y": 5500, "w": 2500, "h": 2500},
    {"name": "다용도실", "x": 12000, "y": 12000, "w": 2000, "h": 2000},  # 완전 외곽 밖 → 통과 금지
    {"name": "쪽방",  "x": 9900, "y": 7900, "w": 100,  "h": 100},        # 클립 후 <1㎡ → drop
]
rooms, walls = _rects_to_rooms_and_walls(rects, BOUNDARY)
names = [r["name"] for r in rooms]
print(f"[1 변환] 생존 방={len(rooms)} {names} / 벽={len(walls)}")
assert walls, "벽이 안 생김"
assert walls_on_grid(walls), "벽 좌표가 100mm 격자 아님"
room_pts = [[r["cx"], r["cy"]] for r in rooms]
assert inside_bbox(room_pts, BOUNDARY) and inside_bbox(
    [p for w in walls for p in (w["a"], w["b"])], BOUNDARY), "벽/방이 외곽 밖"

# 외곽 밖 rect(12000,12000)·<1㎡ rect 통과 0
assert "다용도실" not in names, "외곽 완전 밖 rect가 통과됨"
assert "쪽방" not in names, "<1㎡ rect가 안 버려짐"

# 침실 수 = 입력(2) 보존
assert names.count("침실") == 2, f"침실 수 불일치: {names.count('침실')} (기대 2)"

# rooms 중심이 외곽 폴리곤 내부
assert all(point_in_poly([r["cx"], r["cy"]], BOUNDARY) for r in rooms), "방 중심이 외곽 밖"

# 인접 공유변 dedup → 벽 수 < 4 × 방수 (공유 없으면 4×N)
assert len(walls) < 4 * len(rooms), f"공유변 dedup 안 됨: 벽{len(walls)} >= 4×{len(rooms)}"
print(f"[1 dedup] 벽 {len(walls)} < 4×{len(rooms)}={4*len(rooms)} ✓")

# 좌표 스냅(3017→3000) 흔적: y=3000 격자선 위 벽 존재
assert any(w["a"][1] == 3000 or w["b"][1] == 3000 for w in walls), "격자 스냅(3017→3000) 흔적 없음"

# ── 2. JSON 파싱 ──
fenced = '```json\n{"rooms":[{"name":"거실","x":0,"y":0,"w":4000,"h":3000}]}\n```'
p1 = _parse_rooms_json(fenced)
print(f"[2 펜스] 결과 {len(p1)}개")
assert len(p1) == 1 and p1[0]["name"] == "거실", "펜스 JSON 파싱 실패"

prose = '여기 있습니다: {"rooms":[{"name":"침실","x":0,"y":0,"w":3000,"h":3000},{"name":"욕실","x":3000,"y":0,"w":2000,"h":2000}]} 끝'
p2 = _parse_rooms_json(prose)
print(f"[2 산문] 결과 {len(p2)}개")
assert len(p2) == 2, "산문 섞인 응답 파싱 실패"

broken = _parse_rooms_json("JSON 아님")
print(f"[2 깨짐] 결과 {len(broken)}개 (0이어야)")
assert len(broken) == 0, "깨진 응답이 빈 리스트 아님"

# w/h 음수·0 제외
neg = _parse_rooms_json('{"rooms":[{"name":"X","x":0,"y":0,"w":0,"h":100},{"name":"Y","x":0,"y":0,"w":100,"h":100}]}')
print(f"[2 w0제외] 결과 {len(neg)}개 (1이어야)")
assert len(neg) == 1, "w=0 방이 안 걸러짐"

# ── 3. 면적 기반 기본값 ──
for a, er, eb in [(40, 2, 1), (70, 3, 2), (120, 4, 2)]:
    r, bth = _default_room_counts(a)
    print(f"[3 기본값] {a}㎡ → 침실{r} 욕실{bth}")
    assert (r, bth) == (er, eb), f"{a}㎡ 기본값 불일치"

# ── 4. 프롬프트에 개수·직사각형·빈틈금지 명시 ──
prompt = _build_layout_prompt(BOUNDARY, (0, 0, 10000, 8000), 80.0, "A", 2, 1)
print(f"[4 프롬프트] '침실 2개'={'침실 2개' in prompt} '욕실 1개'={'욕실 1개' in prompt}")
assert "침실 2개" in prompt and "욕실 1개" in prompt, "프롬프트에 개수 미명시"
assert "빈틈" in prompt, "프롬프트에 빈틈 규칙 미명시"

print("🎉 백엔드 단독 검증(직사각형 방식) 전 항목 통과")
