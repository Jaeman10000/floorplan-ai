# -*- coding: utf-8 -*-
"""A-2 프로토타입 — '정형 영역 규격 자동 분할' 알고리즘 측정 (구현 전 검증).
AI 좌표를 버리고 코드가 직사각형을 규격 맞춰 분할. 측정:
  · 실제 A세대 내접 직사각형(_max_inscribed_rect 재사용) 치수·면적
  · 두 분할 방식(squarified treemap / 재귀 guillotine strip)으로 침실2·욕실1+거실+주방 분할
  · 각 칸 규격 충족(_ARCH: 침실 폭2400/7㎡, 욕실 1500×2000/3㎡, 거실 minside3300/12㎡, 주방 1800)
  · 빈틈 없음(칸 면적합 == 사각형 면적), 못 넣으면 거부, 계산시간(<200ms 목표)
하드코딩 금지: 실제 A외곽 + 합성 직사각형(정형 큰/작은)으로 시험."""
import sys, os, json, time, math
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import server as S
from shapely.geometry import Polygon

# ── 건축 규격 (server._ARCH 그대로) ─────────────────────────────────────────
ARCH = S._ARCH
GRID = 100

# 방 프로그램: (카테고리, 표시이름, 가중치) — 가중치로 면적 비례 배분(거실 큼)
def program(nbed, nbath):
    prog = [("living", "거실", 2.2)]
    for i in range(nbed):
        prog.append(("bedroom", f"침실{i+1}", 1.35))
    prog.append(("kitchen", "주방", 1.0))
    for i in range(nbath):
        prog.append(("bath", f"욕실{i+1}" if nbath > 1 else "욕실", 0.6))
    return prog


def spec_ok(cat, w, h):
    """칸(w×h mm)이 카테고리 규격 충족? (거실=긴변, 그 외=짧은변 기준)"""
    spec = ARCH.get(cat, ARCH["other"])
    short, long = min(w, h), max(w, h)
    side = long if cat == "living" else short
    area = (w * h) / 1e6
    okw = (spec["min_w"] == 0) or (side >= spec["min_w"] - 1)
    oka = (spec["min_area"] == 0) or (area >= spec["min_area"] - 0.05)
    return okw, oka, side, area


# ── 방식 1: Squarified treemap ──────────────────────────────────────────────
def _worst(row, length, total_area_per_len):
    """row(면적 리스트)를 length 변에 깔 때 최악 종횡비."""
    s = sum(row)
    if s <= 0 or length <= 0:
        return float("inf")
    side = s / length            # 이 행의 두께
    mx = max(row); mn = min(row)
    w_max = mx / side; w_min = mn / side
    return max((length * length * mx) / (s * s), (s * s) / (length * length * mn))


def squarify(areas, x, y, w, h):
    """면적 리스트 → [(x,y,w,h)] 칸. 표준 squarified(종횡비 1에 가깝게)."""
    rects = []
    items = list(areas)
    rx, ry, rw, rh = x, y, w, h
    while items:
        length = min(rw, rh)
        row = [items[0]]
        rest = items[1:]
        while rest:
            cand = row + [rest[0]]
            if _worst(cand, length, None) <= _worst(row, length, None):
                row = cand; rest = rest[1:]
            else:
                break
        # row를 짧은 변(length)에 깔기
        s = sum(row)
        if rw <= rh:
            thick = s / rw  # 가로로 한 줄
            cy = ry
            for a in row:
                cw = rw
                ch = a / cw
                rects.append((rx, cy, cw, ch))
                cy += ch
            ry += thick; rh -= thick
        else:
            thick = s / rh
            cx = rx
            for a in row:
                ch = rh
                cw = a / ch
                rects.append((cx, ry, cw, ch))
                cx += cw
            rx += thick; rw -= thick
        items = rest
    return rects


def partition_squarify(prog, rect):
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    A = W * H
    wsum = sum(p[2] for p in prog)
    # 가중치 비례 면적, 단 min_area 보장(부족하면 나중에 거부 판정)
    areas = []
    for cat, nm, wt in prog:
        a = A * wt / wsum
        areas.append(a)
    rects = squarify(areas, x0, y0, W, H)
    out = []
    for (cat, nm, wt), (rx, ry, rw, rh) in zip(prog, rects):
        out.append({"cat": cat, "name": nm, "x": rx, "y": ry, "w": rw, "h": rh})
    return out


# ── 방식 2: 재귀 guillotine strip (긴 변에서 큰 방부터 strip 절단) ──────────
def partition_guillotine(prog, rect):
    """면적 큰 방부터 사각형의 긴 변을 따라 strip으로 떼어낸다.
    각 strip 폭 = area/strip_height (>= min_w 자연 보장 시도). 마지막 방이 잔여."""
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    A = W * H
    wsum = sum(p[2] for p in prog)
    order = sorted(prog, key=lambda p: -p[2])  # 큰 방부터
    targets = {id(p): A * p[2] / wsum for p in prog}
    rx, ry, rw, rh = x0, y0, W, H
    out = []
    for i, p in enumerate(order):
        cat, nm, wt = p
        if i == len(order) - 1:
            out.append({"cat": cat, "name": nm, "x": rx, "y": ry, "w": rw, "h": rh})
            break
        a = targets[id(p)]
        if rw >= rh:           # 세로 절단(왼쪽 strip 떼기)
            cw = a / rh
            cw = min(cw, rw)
            out.append({"cat": cat, "name": nm, "x": rx, "y": ry, "w": cw, "h": rh})
            rx += cw; rw -= cw
        else:                  # 가로 절단(위 strip)
            ch = a / rw
            ch = min(ch, rh)
            out.append({"cat": cat, "name": nm, "x": rx, "y": ry, "w": rw, "h": ch})
            ry += ch; rh -= ch
    return out


# ── 방식 3: 규격 인지 BSP (분할마다 위반 최소 분기 선택) ───────────────────
def _need_side(cat):
    """이 방이 요구하는 '결정 변' 최소 길이(거실=긴변, 그 외=짧은변)."""
    return ARCH.get(cat, ARCH["other"])["min_w"]


def _cell_violation(cat, w, h):
    okw, oka, _, _ = spec_ok(cat, w, h)
    return (0 if okw else 1) + (0 if oka else 1)


def partition_bsp(prog, rect):
    """재귀 이분할: 방 순서 고정(거실·침실 먼저=외곽쪽), 각 단계에서 분할점 k와
    절단방향(긴 변)을 모두 시도해 '하위 위반 합 최소'를 선택. 규격을 직접 강제하진
    않지만(거부는 feasible로) 위반을 최소화하는 배치를 찾는다."""
    def rec(rooms, x, y, w, h):
        if len(rooms) == 1:
            cat, nm, wt = rooms[0]
            return [{"cat": cat, "name": nm, "x": x, "y": y, "w": w, "h": h}]
        wsum = sum(r[2] for r in rooms)
        best, best_pen = None, float("inf")
        for k in range(1, len(rooms)):
            g1, g2 = rooms[:k], rooms[k:]
            r1 = sum(r[2] for r in g1) / wsum
            for cut in ("v", "h"):
                if cut == "v":
                    w1 = w * r1;
                    if w1 < 1 or w - w1 < 1: continue
                    c1 = rec(g1, x, y, w1, h); c2 = rec(g2, x + w1, y, w - w1, h)
                else:
                    h1 = h * r1
                    if h1 < 1 or h - h1 < 1: continue
                    c1 = rec(g1, x, y, w, h1); c2 = rec(g2, x, y + h1, w, h - h1)
                cells = c1 + c2
                pen = sum(_cell_violation(c["cat"], c["w"], c["h"]) for c in cells)
                # 종횡비 페널티(동률일 때 정사각형 선호)
                pen2 = pen + 0.01 * sum(max(c["w"], c["h"]) / max(1, min(c["w"], c["h"])) for c in cells)
                if pen2 < best_pen:
                    best_pen = pen2; best = cells
        return best if best else [{"cat": r[0], "name": r[1], "x": x, "y": y, "w": w, "h": h} for r in rooms[:1]]
    # 큰 방(거실) 먼저 오도록 정렬
    order = sorted(prog, key=lambda p: -p[2])
    x0, y0, x1, y1 = rect
    return rec(order, x0, y0, x1 - x0, y1 - y0)


# ── 평가 ────────────────────────────────────────────────────────────────────
def evaluate(cells, rect, tag):
    x0, y0, x1, y1 = rect
    A = (x1 - x0) * (y1 - y0)
    asum = sum(c["w"] * c["h"] for c in cells)
    gapless = abs(asum - A) / A < 0.02
    fails = []
    for c in cells:
        okw, oka, side, area = spec_ok(c["cat"], c["w"], c["h"])
        if not okw or not oka:
            fails.append(f"{c['name']}({c['cat']}) "
                         f"{c['w']/1000:.2f}x{c['h']/1000:.2f}m={area:.1f}㎡ "
                         f"{'폭X' if not okw else ''}{'면적X' if not oka else ''} (side {side/1000:.2f})")
    print(f"  [{tag}] 빈틈없음={gapless} 규격위반={len(fails)}")
    for c in cells:
        _, _, side, area = spec_ok(c["cat"], c["w"], c["h"])
        print(f"      {c['name']:<6} {c['w']/1000:5.2f} x {c['h']/1000:5.2f} m = {area:5.1f}㎡  (기준변 {side/1000:.2f}m)")
    for f in fails:
        print(f"      ✗ {f}")
    return gapless, len(fails)


def feasible(prog, rect):
    """min_area 합 > 면적이면 물리적으로 불가 → 거부."""
    x0, y0, x1, y1 = rect
    A = (x1 - x0) * (y1 - y0) / 1e6
    need = sum(ARCH.get(c, ARCH["other"])["min_area"] for c, _, _ in prog)
    # min_area 0인 주방 등엔 최소 4㎡ 가산(실효 면적)
    need += sum(4.0 for c, _, _ in prog if ARCH.get(c, ARCH["other"])["min_area"] == 0)
    return need <= A, need, A


# ══ 1) 실제 A세대 내접 직사각형 ══════════════════════════════════════════════
print("\n[1] 실제 A세대 내접 직사각형 (_max_inscribed_rect 재사용)")
bnd = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_a_boundary.json")))
bpoly = Polygon([(float(x), float(y)) for x, y in bnd])
if not bpoly.is_valid:
    bpoly = bpoly.buffer(0)
print(f"   A외곽 면적 {bpoly.area/1e6:.1f}㎡, 정점 {len(bnd)}")
t0 = time.perf_counter()
ir = S._max_inscribed_rect(bpoly)
t_ir = (time.perf_counter() - t0) * 1000
assert ir, "내접 직사각형 추출 실패"
iw, ih = (ir[2]-ir[0])/1000, (ir[3]-ir[1])/1000
print(f"   내접 직사각형 {iw:.2f} x {ih:.2f} m = {iw*ih:.1f}㎡  (계산 {t_ir:.1f}ms)")
print(f"   잔여(외곽-내접) = {bpoly.area/1e6 - iw*ih:.1f}㎡  ← 비정형 다리, 수동 마감 대상")

# ══ 2) A 내접에 침실2·욕실1+거실+주방 분할 ═══════════════════════════════════
print("\n[2] A 내접 직사각형 분할 — 침실2·욕실1+거실+주방 (5칸)")
progA = program(2, 1)
fa, need, have = feasible(progA, ir)
print(f"   feasible={fa} (필요 최소 {need:.0f}㎡ vs 내접 {have:.1f}㎡)")
t0 = time.perf_counter(); cs_sq = partition_squarify(progA, ir); t_sq = (time.perf_counter()-t0)*1000
g1, f1 = evaluate(cs_sq, ir, f"squarify {t_sq:.1f}ms")
t0 = time.perf_counter(); cs_g = partition_guillotine(progA, ir); t_g = (time.perf_counter()-t0)*1000
g2, f2 = evaluate(cs_g, ir, f"guillotine {t_g:.1f}ms")

# ══ 3) 합성 정형 직사각형 (큰=충분) ══════════════════════════════════════════
print("\n[3] 합성 정형 8.0 x 6.0 m = 48㎡ — 침실2·욕실1+거실+주방")
big = (0, 0, 8000, 6000)
fb, nb, hb = feasible(progA, big)
print(f"   feasible={fb} (필요 {nb:.0f}㎡ vs {hb:.1f}㎡)")
evaluate(partition_squarify(progA, big), big, "squarify")
evaluate(partition_guillotine(progA, big), big, "guillotine")
t0 = time.perf_counter(); cs_b = partition_bsp(progA, big); t_b = (time.perf_counter()-t0)*1000
evaluate(cs_b, big, f"BSP규격인지 {t_b:.1f}ms")

print("\n[3b] 합성 정형 10.0 x 7.0 m = 70㎡ — 침실3·욕실2+거실+주방 (BSP)")
big2 = (0, 0, 10000, 7000)
prog2 = program(3, 2)
fb2, nb2, hb2 = feasible(prog2, big2)
print(f"   feasible={fb2} (필요 {nb2:.0f}㎡ vs {hb2:.1f}㎡)")
t0 = time.perf_counter(); cs_b2 = partition_bsp(prog2, big2); t_b2 = (time.perf_counter()-t0)*1000
evaluate(cs_b2, big2, f"BSP {t_b2:.1f}ms")

# ══ 4) 작은 면적 → 침실3 거부 ════════════════════════════════════════════════
print("\n[4] 작은 정형 4.0 x 3.5 m = 14㎡ — 침실3·욕실1 (거부 기대)")
small = (0, 0, 4000, 3500)
progS = program(3, 1)
fs, ns, hs = feasible(progS, small)
print(f"   feasible={fs} (필요 최소 {ns:.0f}㎡ vs {hs:.1f}㎡) → {'배치 시도' if fs else '★거부(우겨넣지 않음)'}")

# ══ 5) 중간 정형 → 침실2는 되고 침실3은 거부 경계 ════════════════════════════
print("\n[5] 중간 정형 6.0 x 5.0 m = 30㎡ — 침실2 vs 침실3 경계")
mid = (0, 0, 6000, 5000)
for nb_ in (2, 3):
    pr = program(nb_, 1)
    fok, nn, hh = feasible(pr, mid)
    print(f"   침실{nb_}: feasible={fok} (필요 {nn:.0f}㎡ vs {hh:.1f}㎡)")
    if fok:
        evaluate(partition_squarify(pr, mid), mid, f"squarify 침실{nb_}")

print("\n=== 측정 종료 ===")
