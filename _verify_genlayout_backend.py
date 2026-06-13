"""
AI 구조 초안 생성 — 백엔드 단독 검증 (API 키 불필요: 후처리/기본값만).

1. _postprocess_walls: 100mm 격자 스냅 · degenerate(<100mm) 제거 · dedup · 외곽 클립.
   - 외곽 밖 벽은 클립으로 제거(또는 안쪽만 남김), 모든 좌표 100mm 배수.
2. _parse_walls_json: 펜스/산문 섞인 응답에서 walls 추출, 깨진 JSON은 [].
3. _default_room_counts: 면적 기반 기본값.
4. 빈 결과 처리: 후처리 결과 0개면 walls 빈 리스트.
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from server import (_postprocess_walls, _parse_walls_json, _default_room_counts,
                    _snap_grid, _build_layout_prompt)

# 6m × 4m 직사각 외곽 (mm)
BOUNDARY = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def all_on_grid(walls):
    for w in walls:
        for p in (w["a"], w["b"]):
            if p[0] % 100 != 0 or p[1] % 100 != 0:
                return False
    return True


def inside_bbox(walls, b, tol=60):  # buffer(50) 클립 → 약간 여유
    xs = [p[0] for p in b]; ys = [p[1] for p in b]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    for w in walls:
        for p in (w["a"], w["b"]):
            if not (x0 - tol <= p[0] <= x1 + tol and y0 - tol <= p[1] <= y1 + tol):
                return False
    return True


# ── 1. 후처리: 격자 스냅 + 안쪽 벽 ──
raw = [
    {"a": [3017, 33], "b": [3017, 3988]},     # 거의 격자, 세로 가운데 벽 → 3000으로 스냅
    {"a": [10, 2000], "b": [5990, 2000]},     # 가로 벽
]
pp = _postprocess_walls(raw, BOUNDARY)
print(f"[1 후처리] 입력2 → 결과 {len(pp)}개, 격자정렬={all_on_grid(pp)}, 외곽내={inside_bbox(pp, BOUNDARY)}")
assert len(pp) >= 1, "정상 벽이 사라짐"
assert all_on_grid(pp), "좌표가 100mm 격자가 아님"
assert inside_bbox(pp, BOUNDARY), "벽이 외곽 밖"
# 3017 → 3000 스냅 확인
assert any(w["a"][0] == 3000 or w["b"][0] == 3000 for w in pp), "격자 스냅(3017→3000) 안 됨"

# ── 1b. degenerate 제거 ──
deg = _postprocess_walls([{"a": [1000, 1000], "b": [1030, 1000]}], BOUNDARY)  # 30mm
print(f"[1b degenerate] 30mm 벽 → 결과 {len(deg)}개 (0이어야)")
assert len(deg) == 0, "degenerate(<100mm) 벽이 안 걸러짐"

# ── 1c. 외곽 밖 벽 클립 ──
outside = _postprocess_walls([{"a": [8000, 8000], "b": [9000, 8000]}], BOUNDARY)  # 완전 밖
print(f"[1c 외곽밖] 완전 밖 벽 → 결과 {len(outside)}개 (0이어야)")
assert len(outside) == 0, "외곽 완전 밖 벽이 클립으로 안 사라짐"

# 일부만 밖 → 안쪽만 남음
partial = _postprocess_walls([{"a": [3000, 2000], "b": [9000, 2000]}], BOUNDARY)
print(f"[1c 일부밖] 절반 밖 벽 → 결과 {len(partial)}개, 외곽내={inside_bbox(partial, BOUNDARY)}")
assert len(partial) >= 1 and inside_bbox(partial, BOUNDARY), "일부 밖 벽의 안쪽 구간이 안 남음"

# ── 1d. dedup ──
dup = _postprocess_walls([
    {"a": [1000, 1000], "b": [1000, 3000]},
    {"a": [1000, 3000], "b": [1000, 1000]},  # 같은 벽(방향만 반대)
], BOUNDARY)
print(f"[1d dedup] 같은 벽 2개 → 결과 {len(dup)}개 (1이어야)")
assert len(dup) == 1, "방향만 다른 동일 벽이 dedup 안 됨"

# ── 2. JSON 파싱 ──
fenced = '```json\n{"walls":[{"a":[0,0],"b":[1000,0]}]}\n```'
p1 = _parse_walls_json(fenced)
print(f"[2 펜스파싱] 결과 {len(p1)}개")
assert len(p1) == 1, "펜스 감싼 JSON 파싱 실패"

prose = '여기 결과입니다: {"walls":[{"a":[0,0],"b":[1000,0]},{"a":[1000,0],"b":[1000,1000]}]} 끝.'
p2 = _parse_walls_json(prose)
print(f"[2 산문혼합] 결과 {len(p2)}개")
assert len(p2) == 2, "산문 섞인 응답에서 첫 {...} 추출 실패"

broken = _parse_walls_json("이건 JSON이 아닙니다")
print(f"[2 깨진응답] 결과 {len(broken)}개 (0이어야)")
assert len(broken) == 0, "깨진 응답이 빈 리스트가 아님"

# ── 3. 면적 기반 기본값 ──
for a, er, eb in [(40, 2, 1), (70, 3, 2), (120, 4, 2)]:
    r, bth = _default_room_counts(a)
    print(f"[3 기본값] {a}㎡ → 방{r} 화장실{bth}")
    assert (r, bth) == (er, eb), f"{a}㎡ 기본값 불일치: 방{r}화{bth} (기대 방{er}화{eb})"

# ── 4. 프롬프트에 개수 명시 ──
prompt = _build_layout_prompt(BOUNDARY, (0, 0, 6000, 4000), 24.0, "A", 3, 2)
print(f"[4 프롬프트] '방 3개' 포함={'방 3개' in prompt} '화장실 2개' 포함={'화장실 2개' in prompt}")
assert "방 3개" in prompt and "화장실 2개" in prompt, "프롬프트에 개수 미명시"

print("🎉 백엔드 단독 검증 전 항목 통과")
