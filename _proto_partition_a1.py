# -*- coding: utf-8 -*-
"""A-1 프로토타입 — 비정형 외곽 자체를 규격 맞춰 분할할 수 있는가? (측정만, 본 구현 X).
비정형 직사각형 분할(non-convex rectangular partitioning)은 학술적으로도 어려움 →
실제 A세대 ㄱ자(75.6㎡, 채움0.46)에서 되는지 3방식으로 측정하고 안 되면 멈춘다.

방식:
  a. 직사각형 분해 + BSP: 외곽을 최대내접 사각형들로 덮은 뒤(_max_inscribed_rect 반복),
     각 조각에 방을 배분해 _partition_bsp.
  b. inscribe-and-carve (그리드 채우기 유사): 방을 큰 것부터, 남은 영역의 최대내접 사각형에서
     목표 크기 sub-rect를 카빙(폭≥min_w 보장)하고 빼기 반복.
  c. 최대내접 반복(maximal rect): 각 방이 남은 영역의 최대내접 사각형을 통째로 차지.
측정: 방 배치 성공/요청, 규격위반(폭·면적·채광), 채움비율, 외곽밖 면적, 시간, 침실3 거부.
모든 기하는 server 함수 재사용(_max_inscribed_rect/_ARCH/_edge_directions/_touches_outer)."""
import sys, os, json, time, math
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import server as S
from shapely.geometry import Polygon, MultiPolygon

ARCH = S._ARCH

# 방 프로그램(카테고리·이름·목표가중치) — A-2와 동일 가중치
def program(nbed, nbath):
    prog = [("living", "거실", 2.2)]
    for i in range(nbed):
        prog.append(("bedroom", f"침실{i+1}", 1.35))
    prog.append(("kitchen", "주방", 1.0))
    for i in range(nbath):
        prog.append(("bath", f"욕실{i+1}" if nbath > 1 else "욕실", 0.6))
    return prog


def spec_check(cat, w, h):
    spec = ARCH.get(cat, ARCH["other"])
    short, long = min(w, h), max(w, h)
    side = long if cat == "living" else short
    area = (w * h) / 1e6
    okw = (spec["min_w"] == 0) or (side >= spec["min_w"] - 1)
    oka = (spec["min_area"] == 0) or (area >= spec["min_area"] - 0.05)
    return okw, oka


def min_dims(cat):
    """이 방을 놓기 위한 최소 (짧은변, 긴변) mm."""
    spec = ARCH.get(cat, ARCH["other"])
    mw = spec["min_w"]
    ma = spec["min_area"] * 1e6
    if cat == "living":          # 긴변≥min_w, 면적 만족
        long_min = mw
        short_min = (ma / long_min) if long_min else 1000
    else:
        short_min = mw if mw else 1000
        long_min = (ma / short_min) if short_min else 1000
    return short_min, max(long_min, short_min)


def rect_poly(x, y, w, h):
    return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


def carve_room(cat, region, target_area):
    """region의 최대내접 사각형에서 방 하나를 카빙. (room_rect or None, ir_dims).
    폭≥min_w 보장하도록 사각형 치수 선택, target_area 면적 목표(내접 한계 내)."""
    ir = S._max_inscribed_rect(region)
    if not ir:
        return None, None
    ix0, iy0, ix1, iy1 = ir
    iw, ih = ix1 - ix0, iy1 - iy0
    smin, lmin = min_dims(cat)
    # 내접이 최소치수도 못 담으면 실패
    if max(iw, ih) < lmin - 1 or min(iw, ih) < smin - 1:
        return None, (iw, ih)
    # 카빙 치수: 폭(짧은 쪽)을 min_w 이상으로, 면적 target에 맞춰 길이 결정
    # 긴 변 방향으로 길이를 잡는다.
    if iw >= ih:
        h = ih
        w = target_area / h if h else iw
        w = max(w, smin if cat != "living" else (target_area/h if h else smin))
        w = min(max(w, smin), iw)
        # 거실은 긴변(=w 여기선 가로) 기준
        room = (ix0, iy0, w, h)
    else:
        w = iw
        h = target_area / w if w else ih
        h = min(max(h, smin), ih)
        room = (ix0, iy0, w, h)
    return room, (iw, ih)


# ── 방식 b/c 공통: inscribe-and-carve ───────────────────────────────────────
def approach_carve(prog, poly, full_take=False):
    """full_take=False(b): 목표 크기 카빙 / True(c): 최대내접 통째."""
    region = poly
    total = poly.area
    wsum = sum(p[2] for p in prog)
    placed = []
    order = sorted(prog, key=lambda p: -p[2])
    fails = []
    for cat, nm, wt in order:
        target = total * wt / wsum
        if full_take:
            ir = S._max_inscribed_rect(region)
            if not ir:
                fails.append(nm); continue
            ix0, iy0, ix1, iy1 = ir
            room = (ix0, iy0, ix1 - ix0, iy1 - iy0)
        else:
            room, irdims = carve_room(cat, region, target)
            if room is None:
                fails.append(nm); continue
        rx, ry, rw, rh = room
        rp = rect_poly(rx, ry, rw, rh)
        placed.append({"cat": cat, "name": nm, "rect": room, "poly": rp})
        region = region.difference(rp).buffer(0)
    return placed, fails


# ── 방식 a: 직사각형 분해 + BSP ─────────────────────────────────────────────
def decompose_rects(poly, min_piece_m2=4.0, max_pieces=6):
    """외곽을 최대내접 사각형들로 덮는다(겹침 없이 빼며 반복)."""
    region = poly
    pieces = []
    for _ in range(max_pieces):
        if region.is_empty or region.area < min_piece_m2 * 1e6:
            break
        ir = S._max_inscribed_rect(region)
        if not ir:
            break
        ix0, iy0, ix1, iy1 = ir
        if (ix1-ix0)*(iy1-iy0) < min_piece_m2 * 1e6:
            break
        pieces.append(ir)
        region = region.difference(rect_poly(ix0, iy0, ix1-ix0, iy1-iy0)).buffer(0)
    return pieces, region


def approach_decompose_bsp(prog, poly):
    pieces, leftover = decompose_rects(poly)
    if not pieces:
        return [], [p[1] for p in prog], 0
    # 조각을 면적 큰 순, 방을 가중치 큰 순으로 — 조각에 방을 feasibility로 채워 BSP
    pieces = sorted(pieces, key=lambda r: -((r[2]-r[0])*(r[3]-r[1])))
    rooms_left = sorted(prog, key=lambda p: -p[2])
    placed = []
    fails = []
    pi = 0
    while rooms_left and pi < len(pieces):
        piece = pieces[pi]; pi += 1
        # 이 조각에 들어갈 방을 가능한 만큼(feasibility) 모음
        subset = []
        for r in list(rooms_left):
            trial = subset + [r]
            need, have, ok = S._partition_feasible(trial, piece)
            if ok:
                subset = trial
        if not subset:
            # 조각이 가장 작은 방 하나도 못 담으면 스킵
            continue
        for r in subset:
            rooms_left.remove(r)
        cells = S._partition_bsp(subset, piece)
        for c in cells:
            placed.append({"cat": c["cat"], "name": c["name"],
                           "rect": (c["x"], c["y"], c["w"], c["h"]),
                           "poly": rect_poly(c["x"], c["y"], c["w"], c["h"])})
    fails = [r[1] for r in rooms_left]
    return placed, fails, len(pieces)


# ── 평가 ────────────────────────────────────────────────────────────────────
def evaluate(placed, fails, poly, tag, dt_ms, extra=""):
    pa = poly.area
    viol = 0
    detail = []
    out_area = 0.0
    for r in placed:
        cat = r["cat"]; rx, ry, rw, rh = r["rect"]
        okw, oka = spec_check(cat, rw, rh)
        # 채광
        day_ok = True
        spec = ARCH.get(cat, ARCH["other"])
        if spec["daylight"]:
            day_ok = S._touches_outer(r["poly"], poly)
        v = (0 if okw else 1) + (0 if oka else 1) + (0 if day_ok else 1)
        viol += v
        # 외곽 밖 면적(카빙은 내접 기반이라 거의 0이어야)
        try:
            outside = r["poly"].difference(poly).area
        except Exception:
            outside = 0.0
        out_area += outside
        flag = ("" if v == 0 else
                f" ✗{'폭' if not okw else ''}{'면적' if not oka else ''}{'채광' if not day_ok else ''}")
        detail.append(f"      {r['name']:<6} {rw/1000:5.2f}x{rh/1000:5.2f}m={rw*rh/1e6:5.1f}㎡{flag}")
    placed_area = sum(r["rect"][2]*r["rect"][3] for r in placed)
    fill = placed_area / pa if pa else 0
    print(f"  [{tag}] 배치 {len(placed)}/{len(placed)+len(fails)} 위반 {viol} 채움 {fill*100:4.1f}% "
          f"외곽밖 {out_area/1e6:.2f}㎡ {dt_ms:5.1f}ms {extra}")
    for d in detail:
        print(d)
    if fails:
        print(f"      미배치: {', '.join(fails)}")
    return {"placed": len(placed), "fails": len(fails), "viol": viol, "fill": fill,
            "out": out_area/1e6, "ms": dt_ms}


def run_shape(name, poly, nbed=2, nbath=1):
    print(f"\n{'='*70}\n[{name}] 면적 {poly.area/1e6:.1f}㎡, 채움 "
          f"{poly.area/((poly.bounds[2]-poly.bounds[0])*(poly.bounds[3]-poly.bounds[1])):.2f}, "
          f"침실{nbed}·욕실{nbath}")
    prog = program(nbed, nbath)
    results = {}
    t0 = time.perf_counter(); pl, fa, npc = approach_decompose_bsp(prog, poly); dt = (time.perf_counter()-t0)*1000
    results["a.분해+BSP"] = evaluate(pl, fa, poly, "a.분해+BSP", dt, f"(조각 {npc}개)")
    t0 = time.perf_counter(); pl, fa = approach_carve(prog, poly, full_take=False); dt = (time.perf_counter()-t0)*1000
    results["b.carve목표"] = evaluate(pl, fa, poly, "b.carve목표", dt)
    t0 = time.perf_counter(); pl, fa = approach_carve(prog, poly, full_take=True); dt = (time.perf_counter()-t0)*1000
    results["c.최대내접반복"] = evaluate(pl, fa, poly, "c.최대내접반복", dt)
    return results


# ══ 형상들 ═══════════════════════════════════════════════════════════════════
shapes = {}
# 실제 A세대 (severe ㄱ자, 채움 0.46)
bnd = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_a_boundary.json")))
A = Polygon([(float(x), float(y)) for x, y in bnd]); A = A if A.is_valid else A.buffer(0)
shapes["A세대(실제 ㄱ자)"] = A
# 합성 직사각형 (같은 면적, 정형 — 대조군)
shapes["합성 직사각 9.0x8.4=75.6㎡"] = rect_poly(0, 0, 9000, 8400)
# 합성 완만 ㄱ자 (10x8 − 3x3 노치 = 71㎡, 채움 0.89)
gentle = rect_poly(0, 0, 10000, 8000).difference(rect_poly(7000, 5000, 3000, 3000))
shapes["합성 완만ㄱ자 71㎡(채움0.89)"] = gentle

allres = {}
for nm, poly in shapes.items():
    allres[nm] = run_shape(nm, poly, 2, 1)

# 침실3 거부 측정 (A세대)
print(f"\n{'='*70}\n[A세대 침실3·욕1 — 거부 동작 측정]")
run_shape("A세대 침실3", A, 3, 1)

# ── 요약 표 ──────────────────────────────────────────────────────────────────
print(f"\n{'='*70}\n요약 (침실2·욕1)")
print(f"{'형상':<26}{'방식':<14}{'배치':>6}{'위반':>5}{'채움%':>7}{'밖㎡':>7}{'ms':>7}")
for shp, res in allres.items():
    for meth, r in res.items():
        print(f"{shp:<26}{meth:<14}{r['placed']:>4}/{r['placed']+r['fails']:<2}{r['viol']:>4}"
              f"{r['fill']*100:>6.1f} {r['out']:>6.2f}{r['ms']:>7.1f}")

print("\n=== 측정 종료 ===")
