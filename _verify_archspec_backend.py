# -*- coding: utf-8 -*-
"""GET /api/arch-spec 단일 소스 검증 — 응답이 server._ARCH 숫자·_room_category 키워드와
정확히 일치하는지(드리프트 0). + 리팩터된 _room_category 동작 동일성."""
import sys, os, asyncio, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import server as S

PASS, FAIL = [], []
def ck(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  OK  " if cond else " FAIL ") + name)


# ══ 1) 엔드포인트 응답 == _ARCH ═══════════════════════════════════════════════
print("\n[1] arch-spec specs == _ARCH")
resp = json.loads(asyncio.run(S.arch_spec()).body)
ck("specs 키가 _ARCH와 동일", set(resp["specs"].keys()) == set(S._ARCH.keys()))
drift = []
for cat, spec in S._ARCH.items():
    r = resp["specs"].get(cat, {})
    for k in ("min_w", "min_area", "daylight"):
        if r.get(k) != spec.get(k):
            drift.append(f"{cat}.{k}: {r.get(k)} != {spec.get(k)}")
ck("specs 값 드리프트 0", not drift)
if drift:
    print("   ", drift)
# 사용자 명시 규격 숫자 확인
ck("침실 2400/7", S._ARCH["bedroom"]["min_w"] == 2400 and S._ARCH["bedroom"]["min_area"] == 7.0)
ck("욕실 1500/3", S._ARCH["bath"]["min_w"] == 1500 and S._ARCH["bath"]["min_area"] == 3.0)
ck("거실 3300/12", S._ARCH["living"]["min_w"] == 3300 and S._ARCH["living"]["min_area"] == 12.0)
ck("주방 1800", S._ARCH["kitchen"]["min_w"] == 1800)


# ══ 2) categories == _CATEGORY_KEYWORDS, 순서 보존 ════════════════════════════
print("\n[2] arch-spec categories == _CATEGORY_KEYWORDS")
resp_cats = [(c["cat"], c["keywords"]) for c in resp["categories"]]
ck("categories 순서·키워드 일치", resp_cats == [(cat, kws) for cat, kws in S._CATEGORY_KEYWORDS])
ck("주방이 침실보다 먼저(주방⊃방)",
   [c for c, _ in resp_cats].index("kitchen") < [c for c, _ in resp_cats].index("bedroom"))


# ══ 3) 프런트 roomCategoryOf 재현이 _room_category와 일치 (대표 이름) ══════════
print("\n[3] 카테고리 매칭 일치 (프런트 로직 모사 vs 백엔드)")
def js_room_category(name, cats):
    nu = (name or "").strip().upper()
    if not nu:
        return "other"
    for cat, kws in cats:
        if any(k.upper() in nu for k in kws):
            return cat
    return "other"

names = ["주방", "부엌", "거실", "LDK", "ldk", "리빙", "욕실", "화장실", "세면대", "샤워실",
         "다용도실", "팬트리", "창고", "드레스룸", "보일러실", "세탁실", "현관",
         "발코니", "베란다", "침실", "침실1", "안방", "방", "작은방", "테라스", "", "거실 겸 주방"]
mism = []
for nm in names:
    be = S._room_category(nm)
    fe = js_room_category(nm, [(c["cat"], c["keywords"]) for c in resp["categories"]])
    if be != fe:
        mism.append(f"{nm!r}: BE={be} FE={fe}")
print(f"   대표 {len(names)}개 이름 검사")
ck("프런트 재현 == 백엔드 _room_category", not mism)
if mism:
    print("   ", mism)
# 핵심 케이스
ck("'안방'→bedroom", S._room_category("안방") == "bedroom")
ck("'거실 겸 주방'→kitchen(주방 먼저)", S._room_category("거실 겸 주방") == "kitchen")
ck("'드레스룸'→utility(검사 대상 아님)", S._room_category("드레스룸") == "utility")
ck("'테라스'→other", S._room_category("테라스") == "other")


# ══ 4) spec 0 카테고리는 규격 검사 없음(현관·다용도·발코니·기타) ══════════════
print("\n[4] 검사 제외 카테고리")
for cat in ("entry", "utility", "balcony", "other"):
    s = S._ARCH[cat]
    ck(f"{cat} min_w/min_area 0", s["min_w"] == 0 and s["min_area"] == 0.0)


print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL GREEN")
