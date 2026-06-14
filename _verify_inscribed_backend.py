# -*- coding: utf-8 -*-
"""최대 내접 직사각형 앵커 + 산문 방지 별도 예산 + 용량/형상 경고 — 백엔드 단위 검증.
AI 호출 없이 caller 주입·직접 함수 호출로:
  _max_inscribed_rect(직사각형≥98%·ㄱ자/삼각형 실영역 내·MultiPolygon·100mm 스냅·성능)
  트리거1(need>avail → AI 호출 전 422)
  트리거2(나쁨×3 → 형상 경고 prepend + best 반환)
  산문/파싱 실패 별도 +2 예산(정상 max_tries 미차감)
  프롬프트 안전영역 줄·산문금지 문구."""
import sys, os, json, time, asyncio
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
from shapely.geometry import Polygon, MultiPolygon
import server as S
from fastapi import HTTPException

PASS, FAIL = [], []
def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else " FAIL ") + name)

def rect_poly(x, y, w, h):
    return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


# ══ 1) _max_inscribed_rect ═══════════════════════════════════════════════════
print("\n[1] _max_inscribed_rect")

# 1a) 직사각형 → 내접 ≈ bbox (≥98%)
rectR = rect_poly(0, 0, 8000, 6000)
t0 = time.perf_counter()
ir = S._max_inscribed_rect(rectR)
dt = (time.perf_counter() - t0) * 1000
ck("직사각형 None 아님", ir is not None)
if ir:
    ia = (ir[2] - ir[0]) * (ir[3] - ir[1]) / 1e6
    ratio = ia / (rectR.area / 1e6)
    print(f"   내접={ia:.1f}㎡ / bbox={rectR.area/1e6:.1f}㎡ = {ratio*100:.0f}%  ({dt:.0f}ms)")
    ck("직사각형 내접 ≥98%", ratio >= 0.98)
    ck("직사각형 100mm 격자 스냅", all(v % 100 == 0 for v in ir))
    ck("직사각형 계산 <100ms", dt < 100)

# 1b) ㄱ자 → 실제 영역 안
Lr = Polygon([(0, 0), (10000, 0), (10000, 4000), (4000, 4000), (4000, 12000), (0, 12000)])
irL = S._max_inscribed_rect(Lr)
ck("ㄱ자 None 아님", irL is not None)
if irL:
    rp = rect_poly(irL[0], irL[1], irL[2] - irL[0], irL[3] - irL[1])
    ck("ㄱ자 내접사각형이 실영역 안", Lr.buffer(1).contains(rp))
    ck("ㄱ자 100mm 스냅", all(v % 100 == 0 for v in irL))

# 1c) 삼각형 → 실제 영역 안 (bbox 절반 이하)
tri = Polygon([(0, 0), (12000, 0), (0, 10000)])
irT = S._max_inscribed_rect(tri)
ck("삼각형 None 아님", irT is not None)
if irT:
    rp = rect_poly(irT[0], irT[1], irT[2] - irT[0], irT[3] - irT[1])
    ck("삼각형 내접사각형이 실영역 안", tri.buffer(1).contains(rp))
    ta = (irT[2] - irT[0]) * (irT[3] - irT[1]) / 1e6
    print(f"   삼각형 내접={ta:.1f}㎡ / bbox={tri.bounds[2]*tri.bounds[3]/1e6:.1f}㎡")

# 1d) MultiPolygon → 한 조각 안
mp = MultiPolygon([rect_poly(0, 0, 5000, 4000), rect_poly(8000, 0, 3000, 3000)])
irM = S._max_inscribed_rect(mp)
ck("MultiPolygon None 아님", irM is not None)
if irM:
    rp = rect_poly(irM[0], irM[1], irM[2] - irM[0], irM[3] - irM[1])
    ck("MultiPolygon 내접이 어느 한 조각 안", mp.buffer(1).contains(rp))

# 1e) 퇴화 → None
ck("퇴화(빈) → None", S._max_inscribed_rect(Polygon()) is None)
ck("초소형(<100mm) → None", S._max_inscribed_rect(rect_poly(0, 0, 50, 50)) is None)

# 1f) 큰 직사각형 성능
big = rect_poly(0, 0, 30000, 20000)
t0 = time.perf_counter()
S._max_inscribed_rect(big)
dt = (time.perf_counter() - t0) * 1000
print(f"   큰 30×20m 계산 {dt:.0f}ms")
ck("큰 도면 <100ms", dt < 100)


# ══ 2) 트리거1: need > avail → AI 호출 전 422 ════════════════════════════════
print("\n[2] 트리거1 (need>avail → 422, AI 호출 전)")
os.environ["ANTHROPIC_API_KEY"] = "dummy-for-trigger1-test"   # 호출 전 422라 실제 호출 안 함

async def call_gen(payload):
    return await S.generate_layout(payload)

# 4×4m=16㎡ 세대에 침실3·욕실1 → need=3*7+12+1*3=36 > 16
small_bnd = [[0, 0], [4000, 0], [4000, 4000], [0, 4000]]
raised = None
try:
    asyncio.run(call_gen({"boundary_mm": small_bnd, "rooms": 3, "baths": 1}))
except HTTPException as e:
    raised = e
ck("작은 세대+많은 방 → 422", raised is not None and raised.status_code == 422)
if raised:
    print(f"   422 detail: {raised.detail}")
    ck("422 메시지에 필요면적 안내", "최소" in raised.detail and "㎡" in raised.detail)

# 충분한 세대(10×8=80㎡, 침실2·욕실1 → need=14+12+3=29<80)는 트리거1 통과(이후 더미키로 AI 호출→500)
big_bnd = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]
err2 = None
try:
    asyncio.run(call_gen({"boundary_mm": big_bnd, "rooms": 2, "baths": 1}))
except HTTPException as e:
    err2 = e
except Exception as e:
    err2 = e
# 트리거1을 통과했으므로 422(용량)는 아니어야 한다(더미키라 AI단계 500 등 다른 오류 OK)
passed_trigger1 = not (isinstance(err2, HTTPException) and err2.status_code == 422
                       and "최소" in (err2.detail or ""))
ck("충분한 세대는 트리거1 통과(용량 422 아님)", passed_trigger1)


# ══ 3) _generate_with_retries: 산문 별도 예산 ════════════════════════════════
print("\n[3] 산문/파싱 실패 별도 +2 예산 (정상 max_tries 미차감)")
OUT = Polygon([(0, 0), (10000, 0), (10000, 8000), (0, 8000)])
BND = [[0, 0], [10000, 0], [10000, 8000], [0, 8000]]
GOOD = json.dumps({"rooms": [
    {"name": "거실", "x": 0, "y": 0, "w": 5000, "h": 4000},
    {"name": "침실1", "x": 5000, "y": 0, "w": 2500, "h": 4000},
    {"name": "침실2", "x": 7500, "y": 0, "w": 2500, "h": 4000},
    {"name": "욕실", "x": 0, "y": 4000, "w": 2000, "h": 2000},
    {"name": "주방", "x": 2000, "y": 4000, "w": 2000, "h": 2000},
    {"name": "현관", "x": 4000, "y": 4000, "w": 2000, "h": 2000},
]})

def make_caller(scripts):
    st = {"i": 0}
    def caller(prompt):
        s = scripts[min(st["i"], len(scripts) - 1)]
        st["i"] += 1
        return s
    return caller, st

# 3a) 산문 ×2 → 좋음: 산문이 정상 시도를 안 까먹어 attempts=1·ok=True
caller, st = make_caller(["산문입니다 no json", "여전히 설명", GOOD])
res = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 2, 1, None, caller, max_tries=3)
ck("3a 결과 있음", res is not None)
if res:
    _, _, _, ok, attempts, _ = res
    print(f"   3a 호출 {st['i']}회 attempts(정상시도)={attempts} ok={ok}")
    ck("3a 좋음 채택(ok=True)", ok is True)
    ck("3a 호출 3회(산문2+좋음)", st["i"] == 3)
    ck("3a 정상시도 1회만 소모(산문 미차감)", attempts == 1)

# 3b) 산문 전부(5회): 예산2 흡수 후 정상3회 소모 → None, 종료 보장
caller2, st2 = make_caller(["x"] * 8)
res2 = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 2, 1, None, caller2, max_tries=3)
print(f"   3b 호출 {st2['i']}회 결과={res2}")
ck("3b 전부 산문 → None", res2 is None)
ck("3b 호출 5회로 종료(예산2+정상3)", st2["i"] == 5)


# ══ 4) 트리거2: 나쁨×3 → 형상 경고 prepend + best 반환 ═══════════════════════
print("\n[4] 트리거2 (침실 폭/면적 HARD 몰림 → 형상 경고)")
# 침실3 전부 폭 2000<2400 → bedroom width HARD ×3, 거실은 채광 OK
NARROW = json.dumps({"rooms": [
    {"name": "거실", "x": 0, "y": 0, "w": 6000, "h": 4000},
    {"name": "침실1", "x": 0, "y": 4000, "w": 2000, "h": 4000},
    {"name": "침실2", "x": 2000, "y": 4000, "w": 2000, "h": 4000},
    {"name": "침실3", "x": 4000, "y": 4000, "w": 2000, "h": 4000},
    {"name": "욕실", "x": 6000, "y": 4000, "w": 2000, "h": 2000},
]})
caller3, st3 = make_caller([NARROW, NARROW, NARROW])
res3 = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 3, 1, None, caller3, max_tries=3)
ck("4 결과 있음", res3 is not None)
if res3:
    room_list3, walls3, warnings3, ok3, attempts3, _ = res3
    print(f"   4 ok={ok3} attempts={attempts3} 방={len(room_list3)} 벽={len(walls3)}")
    print(f"   4 warnings[0]={warnings3[0] if warnings3 else None}")
    ck("4 ok=False(HARD 잔존)", ok3 is False)
    ck("4 best 방/벽 반환", len(room_list3) > 0 and len(walls3) > 0)
    ck("4 warnings 최상단=형상 경고", bool(warnings3) and "형상" in warnings3[0])
    ck("4 형상 경고에 요청 침실수 명시", bool(warnings3) and "침실 3개" in warnings3[0])

# 4b) 좋은 배치는 형상 경고 없음(ok=True라 트리거2 미발동)
caller4, _ = make_caller([GOOD])
res4 = S._generate_with_retries(lambda fb: "p", OUT, OUT, BND, None, 2, 1, None, caller4, max_tries=3)
ck("4b 좋은 배치 ok=True", res4 is not None and res4[3] is True)
ck("4b 좋은 배치 형상 경고 없음", res4 is not None and not any("형상" in w for w in res4[2]))


# ══ 5) 프롬프트: 안전영역 줄 + 산문금지 문구 ═════════════════════════════════
print("\n[5] 프롬프트 문구")
ir5 = S._max_inscribed_rect(OUT)
p = S._build_layout_prompt(BND, (0, 0, 10000, 8000), 80.0, "A", 2, 1, inner_rect=ir5)
ck("프롬프트에 [안전 배치 영역]", "[안전 배치 영역]" in p)
ck("안전영역에 '앵커' 안내", "앵커" in p)
ck("프롬프트 끝 산문금지 재강조", "첫 글자가 '{'" in p)
ck("inner_rect None이면 안전영역 줄 없음",
   "[안전 배치 영역]" not in S._build_layout_prompt(BND, (0, 0, 10000, 8000), 80.0, "A", 2, 1))
ck("_LAYOUT_SYSTEM 첫 글자 '{' 규칙", "첫 글자는 반드시 '{'" in S._LAYOUT_SYSTEM)


print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL GREEN")
