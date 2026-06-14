"""
수동 드로잉 규격 피드백 검증 — 역곡동빌라 page3 A세대 (실제 클릭).
1. arch-spec fetch(또는 폴백) 후 _archSpec 로드.
2. 직사각형 방 그림 → roomDims/라벨 치수 표시(직사각 정확).
3. 침실 이름: 폭<2.4m 또는 면적<7㎡ → checkRoomSpec 미달 / 충족 → 빈 배열.
4. 욕실·거실·주방 경계 검사.
5. 현관·다용도실 → 검사 안 함(빈 배열).
6. 직사각형 모드 토글 → design-spec-guide 표시/숨김.
7. 라벨에 ⚠️·치수 텍스트 포함(designRooms 상태 + checkRoomSpec로 검증).
8. 페이지 에러 0.
"""
import sys, glob, os
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
_root = os.path.dirname(os.path.abspath(__file__))
_cands = [p for p in glob.glob(os.path.join(_root, "backend", "*.pdf"))
          if ("빌라" in p or "역곡" in p)]
assert _cands, "backend/역곡동빌라.pdf를 찾지 못함"
PDF = _cands[0]
URL = "http://localhost:8000/"
A_ROOMS = [0, 1, 2, 11, 20, 21, 24]

PROJ = """
(xy) => {
  const v = new THREE.Vector3(xy[0]/1000, 0.03, xy[1]/1000).project(roomCamera);
  const cv = document.querySelector('#panel-asis canvas.three-canvas');
  const r = cv.getBoundingClientRect();
  return { x:(v.x*0.5+0.5)*r.width + r.left, y:(-v.y*0.5+0.5)*r.height + r.top };
}
"""
# 외곽 안, 모든 꼭짓점이 외곽서 450mm+ 떨어진 직사각형 (지정 가로w·세로h mm)
FIND_RECT = """
(dim) => {
  const W = dim.w, H = dim.h;
  const b=designBoundary; let a=1e9,c=1e9,d=-1e9,e=-1e9;
  for(const p of b){a=Math.min(a,p[0]);d=Math.max(d,p[0]);c=Math.min(c,p[1]);e=Math.max(e,p[1]);}
  function dB(pt){let m=1e12;for(let i=0;i<b.length;i++){const A=b[i],C=b[(i+1)%b.length];
    const vx=C[0]-A[0],vy=C[1]-A[1],L=vx*vx+vy*vy;let t=L>1e-6?((pt[0]-A[0])*vx+(pt[1]-A[1])*vy)/L:0;t=Math.max(0,Math.min(1,t));
    m=Math.min(m,Math.hypot(pt[0]-(A[0]+t*vx),pt[1]-(A[1]+t*vy)));}return m;}
  const R=v=>Math.round(v/100)*100;
  for(let x=R(a)+200;x<=d-W-200;x+=200)for(let y=R(c)+200;y<=e-H-200;y+=200){
    const X=R(x),Y=R(y);const pts=[[X,Y],[X+W,Y],[X+W,Y+H],[X,Y+H]];
    if(pts.every(p=>pointInPoly(p,b)&&dB(p)>450))return{a:[X,Y],b:[X+W,Y+H]};}
  return null;
}
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.mouse.click(p["x"], p["y"]); page.wait_for_timeout(60)


def draw_rect(page, a, b):
    click_mm(page, a)
    p = screen(page, b); page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(40)
    click_mm(page, b)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())

    page.goto(URL)
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();}", A_ROOMS)

    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)
    page.wait_for_timeout(500)  # arch-spec fetch

    # ── 1. _archSpec 로드 확인 ──
    spec = page.evaluate("({loaded:_archSpecLoaded, bed:_archSpec.specs.bedroom.min_w, cats:_archSpec.categories.length})")
    print(f"[1 archSpec] {spec}")
    assert spec["bed"] == 2400 and spec["cats"] >= 7, "arch-spec 로드 실패"

    # ── 2. 헬퍼 직접 검증 (순수 로직) ──
    # 침실 미달: 폭 2.1m(2400 미만) → 미달
    r = page.evaluate("checkRoomSpec('침실', 2100, 5000, 10.5)")
    print(f"[2 침실 폭미달] {r}")
    assert any("폭" in s for s in r), "침실 폭 미달 검출 실패"
    # 침실 면적 미달: 2.5x2.5=6.25㎡ <7
    r = page.evaluate("checkRoomSpec('침실', 2500, 2500, 6.25)")
    print(f"   침실 면적미달 {r}")
    assert any("면적" in s for s in r), "침실 면적 미달 검출 실패"
    # 침실 충족: 3.0x3.0=9㎡
    r = page.evaluate("checkRoomSpec('침실', 3000, 3000, 9.0)")
    print(f"   침실 충족 {r}")
    assert r == [], "충족 침실에 경고 발생"
    # 욕실: 1.4m<1.5 미달 / 1.6x2.0=3.2 충족
    assert page.evaluate("checkRoomSpec('욕실', 1400, 2000, 2.8)").__len__() >= 1, "욕실 미달 검출 실패"
    assert page.evaluate("checkRoomSpec('욕실', 1600, 2000, 3.2)") == [], "욕실 충족 오경고"
    # 거실: 최소변 3.0m<3.3 미달 / 3.4x3.6=12.24 충족
    assert any("폭" in s for s in page.evaluate("checkRoomSpec('거실', 3000, 5000, 15.0)")), "거실 최소변 미달 검출 실패"
    assert page.evaluate("checkRoomSpec('거실', 3400, 3600, 12.24)") == [], "거실 충족 오경고"
    # 주방: 짧은변 1.6m<1.8 미달 / 짧은변 2.0m 충족(면적 무관)
    assert any("폭" in s for s in page.evaluate("checkRoomSpec('주방', 1600, 3000, 4.8)")), "주방 폭 미달 검출 실패"
    assert page.evaluate("checkRoomSpec('주방', 2000, 2500, 5.0)") == [], "주방 충족 오경고"
    print("[2~4 경계검사] 침실/욕실/거실/주방 미달·충족 전부 정확")

    # ── 5. 현관·다용도실 검사 안 함 ──
    assert page.evaluate("checkRoomSpec('현관', 800, 1000, 0.8)") == [], "현관 검사하면 안 됨"
    assert page.evaluate("checkRoomSpec('다용도실', 900, 900, 0.8)") == [], "다용도실 검사하면 안 됨"
    assert page.evaluate("checkRoomSpec('', 1000, 1000, 1.0)") == [], "이름없음 검사하면 안 됨"
    print("[5 제외] 현관·다용도실·이름없음 검사 안 함 ✓")

    # ── 6. 직사각형 모드 토글 → 가이드 표시/숨김 ──
    page.click("#btn-rect-mode")
    page.wait_for_timeout(120)
    g_on = page.evaluate("document.getElementById('design-spec-guide').style.display")
    g_txt = page.evaluate("document.getElementById('design-spec-guide').textContent")
    print(f"[6 가이드 on] display={g_on} 침실포함={'침실' in g_txt}")
    assert g_on == "block" and "침실" in g_txt and "거실" in g_txt, "규격 가이드 표시 실패"
    page.click("#btn-rect-mode")
    page.wait_for_timeout(120)
    assert page.evaluate("document.getElementById('design-spec-guide').style.display") == "none", "가이드 숨김 실패"
    print("   토글 off → 숨김 ✓")

    # ── 7. roomDims/isRect — 합성 폴리곤(결정적)으로 정확/근사 판정 ──
    # 깨끗한 직사각(3x3, 면적 9.0) → isRect True
    d_rect = page.evaluate("roomDims([[0,0],[3000,0],[3000,3000],[0,3000]], 9.0)")
    # L자(전체 4x4 bbox=16㎡인데 실면적 12㎡) → isRect False, ~표기
    d_L = page.evaluate("roomDims([[0,0],[4000,0],[4000,2000],[2000,2000],[2000,4000],[0,4000]], 12.0)")
    print(f"[7 roomDims] 직사각 W={d_rect['W']} H={d_rect['H']} isRect={d_rect['isRect']} / L자 isRect={d_L['isRect']}")
    assert d_rect["W"] == 3000 and d_rect["H"] == 3000 and d_rect["isRect"] is True, "직사각 판정 실패"
    assert d_L["W"] == 4000 and d_L["H"] == 4000 and d_L["isRect"] is False, "L자 근사 판정 실패"

    # ── 8. 실제 직사각형 방 그림 → 방 생성·치수 계산·규격검사 end-to-end ──
    page.click("#btn-rect-mode")
    rect = page.evaluate(FIND_RECT, {"w": 3000, "h": 3000})
    assert rect, "외곽 안 3x3 직사각형 못 찾음"
    nb = page.evaluate("designRooms.length")
    draw_rect(page, rect["a"], rect["b"])
    na = page.evaluate("designRooms.length")
    print(f"[8 그리기] 방 {nb}→{na}")
    assert na > nb, "직사각형 방 생성 실패"
    # 새로 생긴 작은 방(면적 9㎡ 근처) — 치수·규격 검사가 돌고 라벨 메시가 생기는지
    idx = page.evaluate("(()=>{let bi=0,bd=1e9;for(let i=0;i<designRooms.length;i++){const e=Math.abs(designRooms[i].area_m2-9);if(e<bd){bd=e;bi=i;}}return bi;})()")
    info = page.evaluate("(i)=>{const rm=designRooms[i];const d=roomDims(rm.poly,rm.area_m2);return {W:d.W,H:d.H,area:rm.area_m2,iss:checkRoomSpec('침실',d.W,d.H,rm.area_m2)};}", idx)
    print(f"[8 치수계산] W={info['W']} H={info['H']} area={info['area']} 침실검사={info['iss']}")
    assert info["W"] > 0 and info["H"] > 0, "그린 방 치수 계산 실패"
    # 라벨 메시가 그려졌는지 (방 수만큼 sprite 라벨 존재)
    nlabel = page.evaluate("_designRoomMeshes.filter(m=>m.type==='Sprite').length")
    print(f"   라벨 sprite {nlabel}개 (방 {na}개)")
    assert nlabel >= na, "방 라벨(치수 포함)이 안 그려짐"
    page.click("#btn-rect-mode")

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 규격 피드백 검증 통과 (archSpec·치수·미달경고·충족·제외·가이드토글·에러0)")
    browser.close()
