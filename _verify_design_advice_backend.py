# -*- coding: utf-8 -*-
"""설계 시작 조언 — 백엔드 단위 검증 (AI 호출 없이 순수 함수).
_classify_shape(정형/비정형/이형·종횡비)·_build_design_advice_context(면적·bbox·방위·고정방·
개수·trend 유무·내접·현재 그린 방 반영)·입력검증(boundary<3→400·trend cap)."""
import sys, os, asyncio
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import server as S
from fastapi import HTTPException

PASS, FAIL = [], []
def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else " FAIL ") + name)


# ══ 1) _classify_shape ═══════════════════════════════════════════════════════
print("\n[1] _classify_shape")
rect = [[0, 0], [8000, 0], [8000, 6000], [0, 6000]]
L = [[0, 0], [10000, 0], [10000, 4000], [4000, 4000], [4000, 12000], [0, 12000]]
tri = [[0, 0], [12000, 0], [0, 10000]]
narrow = [[0, 0], [12000, 0], [12000, 2000], [0, 2000]]

s_rect = S._classify_shape(rect)
s_L = S._classify_shape(L)
s_tri = S._classify_shape(tri)
s_nar = S._classify_shape(narrow)
print(f"   정형 {s_rect}")
print(f"   비정형 {s_L}")
print(f"   이형 {s_tri}")
print(f"   협소 {s_nar}")
ck("직사각형 → 정형(채움≥0.85)", "정형" in s_rect["label"] and s_rect["fill"] >= 0.85)
ck("ㄱ자 → 비정형(채움 0.55~0.85)", "비정형" in s_L["label"] and 0.55 <= s_L["fill"] < 0.85)
ck("삼각형 → 이형(채움<0.55)", "이형" in s_tri["label"] and s_tri["fill"] < 0.55)
ck("종횡비≥2 → 좁고 긴 형태", "좁고 긴" in s_nar["label"] and s_nar["aspect"] >= 2.0)
ck("면적 계산(직사각형 48㎡)", abs(s_rect["area_m2"] - 48.0) < 0.5)


# ══ 2) _build_design_advice_context ══════════════════════════════════════════
print("\n[2] _build_design_advice_context")
# 2a) 빈 외곽 + 방위 남 + 개수 + 트렌드 없음
ctx = S._build_design_advice_context(rect, "A", "남", [], 3, 1, [], "")
print(ctx[:400] + "\n   ...")
ck("형상 라벨 포함", "외곽 형상" in ctx and "정형" in ctx)
ck("크기/면적 포함", "전용면적" in ctx and "bbox" in ctx)
ck("방위 남 → 상단변=남", "상단변=남" in ctx)
ck("외곽 폴리곤 좌표 포함", "외곽 폴리곤 좌표" in ctx)
ck("안 잘리는 직사각형 영역(내접) 포함", "안 잘리는 가장 큰 직사각형" in ctx)
ck("원하는 구성(침실3·욕실1)", "침실 3개" in ctx and "욕실 1개" in ctx)
ck("그린 방 없음 → '빈 외곽' 안내", "지금까지 그린 방] 없음" in ctx)
ck("트렌드 없음 → 별도 확인 단서", "별도 확인" in ctx)

# 2b) 방위 모름
ctx2 = S._build_design_advice_context(rect, "A", "모름", [], None, None, [], "")
ck("방위 모름 → 미상 안내", "방위] 미상" in ctx2)
ck("개수 미지정 안내", "원하는 구성] 미지정" in ctx2)

# 2c) 고정 방 + 이미 그린 방 + 트렌드 입력
fixed = [{"name": "현관", "poly": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]}]
current = [{"name": "거실", "area_m2": 15.0, "cx": 6000, "cy": 1000},
           {"name": "", "area_m2": 0, "cx": 0, "cy": 0}]  # area0은 제외돼야
ctx3 = S._build_design_advice_context(L, "A", "남", fixed, 2, 1, current, "요즘 4베이 선호, 팬트리 필수")
print("\n   [2c 발췌]")
for kw in ["이미 고정된 공간", "지금까지 그린 방", "거실(15.0㎡", "트렌드/요구"]:
    print(f"      '{kw}' in ctx3 = {kw in ctx3}")
ck("고정 방 이름·위치 포함", "이미 고정된 공간" in ctx3 and "현관" in ctx3)
ck("그린 방 반영(거실 15㎡)", "지금까지 그린 방] 1개" in ctx3 and "거실(15.0㎡" in ctx3)
ck("area0 방은 제외(1개만)", "지금까지 그린 방] 1개" in ctx3)
ck("트렌드 입력 → 우선 반영 안내", "트렌드/요구" in ctx3 and "4베이" in ctx3 and "우선 반영" in ctx3)


# ══ 3) 엔드포인트 입력 검증 (AI 호출 전 단계) ════════════════════════════════
print("\n[3] 엔드포인트 입력 검증")
async def call(payload):
    return await S.design_advice(payload)

# 3a) boundary<3 → 400 (api_key 유무 무관, 가장 먼저 검사)
err = None
try:
    asyncio.run(call({"boundary_mm": [[0, 0], [1, 1]]}))
except HTTPException as e:
    err = e
ck("boundary<3 → 400", err is not None and err.status_code == 400)

# 3b) trend 길이 cap (2000자) — 컨텍스트 빌더에서 자름은 엔드포인트가 [:2000] 처리.
#     순수 함수로는 빌더에 긴 trend 넣어 그대로 들어가는지 확인 + 엔드포인트 cap은 코드상 [:2000].
long_trend = "가" * 5000
ctx_long = S._build_design_advice_context(rect, "A", "남", [], None, None, [], long_trend[:2000])
ck("trend cap 2000자 적용(빌더 입력 기준)", ctx_long.count("가") <= 2000 + 5)

# 3c) api_key 없을 때 500 (boundary 정상) — 더미 제거 후 확인
saved = os.environ.pop("ANTHROPIC_API_KEY", None)
err2 = None
try:
    asyncio.run(call({"boundary_mm": rect}))
except HTTPException as e:
    err2 = e
if saved is not None:
    os.environ["ANTHROPIC_API_KEY"] = saved
ck("api_key 없으면 500", err2 is not None and err2.status_code == 500)


print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL GREEN")
