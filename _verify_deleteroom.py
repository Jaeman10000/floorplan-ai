"""
선택한 방 삭제 검증 — 역곡동빌라 page3 A세대 (실제 클릭).
A세대 ㄱ자 최대 clear 2.8m → 2.8×2.8 영역 안에 방 3개 배치(인접쌍 L/R + 고립 I).
B. 고립 방 선택 삭제 → designRooms 정확히 1 감소·나머지 유지·designWalls 감소·이름 정리. Ctrl+Z 도형 복원·개수 원복.
C. 인접쌍 한쪽 삭제 → 옆 방 면 유지(공유변 보존). Ctrl+Z 복원.
D. 잠긴 방: 삭제 버튼 disabled + Delete 무반응(개수 불변).
E. Delete 키 경로 삭제 / W·H 입력칸 포커스 중엔 무반응.
F. 완전히 둘러싸인 방(3×3 격자 중앙) 삭제 불가 안내·history 불변(로직 검증, 합성).
G. 페이지 에러 0.
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
# W×H 영역 전체가 외곽 안에 깨끗이 들어가는 자리(BL) 찾기 (editroom과 동일 로직).
FIND_RECT = """
(dim) => {
  const W = dim.w, H = dim.h;
  const b=designBoundary; let a=1e9,c=1e9,d=-1e9,e=-1e9;
  for(const p of b){a=Math.min(a,p[0]);d=Math.max(d,p[0]);c=Math.min(c,p[1]);e=Math.max(e,p[1]);}
  function dB(pt){let m=1e12;for(let i=0;i<b.length;i++){const A=b[i],C=b[(i+1)%b.length];
    const vx=C[0]-A[0],vy=C[1]-A[1],L=vx*vx+vy*vy;let t=L>1e-6?((pt[0]-A[0])*vx+(pt[1]-A[1])*vy)/L:0;t=Math.max(0,Math.min(1,t));
    m=Math.min(m,Math.hypot(pt[0]-(A[0]+t*vx),pt[1]-(A[1]+t*vy)));}return m;}
  function clear(X,Y){
    for(let i=0;i<=4;i++)for(let j=0;j<=4;j++){
      if(!pointInPoly([X+W*i/4, Y+H*j/4],b)) return false;
    }
    for(const p of [[X,Y],[X+W,Y],[X+W,Y+H],[X,Y+H]]) if(dB(p)<400) return false;
    for(const v of b) if(v[0]>X-1&&v[0]<X+W+1&&v[1]>Y-1&&v[1]<Y+H+1) return false;
    return true;
  }
  const R=v=>Math.round(v/100)*100;
  for(let x=R(a)+200;x<=d-W-200;x+=100)for(let y=R(c)+200;y<=e-H-200;y+=100){
    const X=R(x),Y=R(y);
    if(clear(X,Y))return{a:[X,Y],b:[X+W,Y+H]};}
  return null;
}
"""

# 중심좌표로 designRooms 인덱스 찾기
FACE_AT = """
(c)=>{ let bi=-1,bd=1e12; for(let i=0;i<designRooms.length;i++){const cc=polyCentroid(designRooms[i].poly);
  const d=Math.hypot(cc[0]-c[0],cc[1]-c[1]); if(d<bd){bd=d;bi=i;}}
  return (bd<400)?bi:-1; }
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.mouse.click(p["x"], p["y"]); page.wait_for_timeout(60)


def draw_rect(page, a, b):
    page.click("#btn-rect-mode"); page.wait_for_timeout(60)
    click_mm(page, a)
    p = screen(page, b); page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(40)
    click_mm(page, b)
    page.click("#btn-rect-mode"); page.wait_for_timeout(60)   # 모드 종료


def face_at(page, c):
    return page.evaluate(FACE_AT, c)


def n_rooms(page):
    return page.evaluate("designRooms.length")


def n_walls(page):
    return page.evaluate("designWalls.length")


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
    page.wait_for_timeout(500)

    # ── 셋업: 2.8×2.8 clear 영역 안에 방 3개 (인접쌍 L/R + 고립 I) ──
    spot = page.evaluate(FIND_RECT, {"w": 2800, "h": 2800})
    assert spot, "2.8×2.8 들어갈 자리 못 찾음"
    X, Y = spot["a"]
    S = 1300        # 방 한 변
    GAP = 500       # 고립 방과 쌍 사이 간격(>스냅 350, 안 붙음)
    # 쌍: L=[X,Y]-[X+S,Y+S], R=[X+S,Y]-[X+2S,Y+S] (가운데 X+S 수직벽 공유)
    Lc = [X + S // 2, Y + S // 2]
    Rc = [X + S + S // 2, Y + S // 2]
    # 고립: 위쪽, 폭 S × 높이 (2800-S-GAP)=1000
    IH = 2800 - S - GAP
    Iy = Y + S + GAP
    Ic = [X + S // 2, Iy + IH // 2]

    draw_rect(page, [X, Y], [X + S, Y + S])               # L
    draw_rect(page, [X + S, Y], [X + 2 * S, Y + S])       # R (공유변)
    draw_rect(page, [X, Iy], [X + S, Iy + IH])            # I (고립)
    nr0 = n_rooms(page)
    print(f"[셋업] 방 {nr0}개 (L={Lc} R={Rc} I={Ic})")
    assert nr0 == 3, f"방 3개 셋업 실패: {nr0}개"
    li, ri, ii = face_at(page, Lc), face_at(page, Rc), face_at(page, Ic)
    assert li >= 0 and ri >= 0 and ii >= 0, f"3방 인덱스 매칭 실패 L={li} R={ri} I={ii}"

    # 고립 방에 이름 붙이기 ('테스트룸')
    page.click("#btn-name-mode"); page.wait_for_timeout(60)
    click_mm(page, Ic)
    page.fill("#design-name-input", "테스트룸")
    page.click("#btn-apply-name"); page.wait_for_timeout(60)
    page.click("#btn-name-mode"); page.wait_for_timeout(60)
    has_name = page.evaluate("designRoomNames.some(rn=>rn.name==='테스트룸')")
    print(f"[셋업] 고립 방 이름 '테스트룸' 등록={has_name}")
    assert has_name, "이름 등록 실패"

    # ── B. 고립 방 삭제 (버튼) → 정확히 1 감소·이름 정리·벽 감소 ──
    page.click("#btn-edit-mode"); page.wait_for_timeout(60)
    click_mm(page, Ic)
    sel = page.evaluate("selectedDesignFace")
    assert sel >= 0, "edit 모드 고립 방 선택 실패"
    del_disabled = page.evaluate("document.getElementById('btn-delete-room').disabled")
    assert del_disabled is False, "안 잠긴 방인데 삭제 버튼 비활성"
    w_before = n_walls(page)
    page.click("#btn-delete-room"); page.wait_for_timeout(120)
    nr1 = n_rooms(page)
    w_after = n_walls(page)
    name_gone = page.evaluate("!designRoomNames.some(rn=>rn.name==='테스트룸')")
    l_ok = face_at(page, Lc) >= 0
    r_ok = face_at(page, Rc) >= 0
    i_gone = face_at(page, Ic) < 0
    print(f"[B 고립삭제] 방 {nr0}→{nr1} 벽 {w_before}→{w_after} 이름정리={name_gone} L유지={l_ok} R유지={r_ok} I사라짐={i_gone}")
    assert nr1 == nr0 - 1, f"designRooms 정확히 1 감소 아님: {nr0}→{nr1}"
    assert w_after < w_before, "designWalls 감소 안 함"
    assert name_gone, "삭제 방 이름이 designRoomNames에 남음(유령 이름)"
    assert l_ok and r_ok and i_gone, "고립만 사라지고 쌍 유지가 안 됨"
    assert page.evaluate("selectedDesignFace") < 0, "삭제 후 선택 해제 안 됨"

    # Ctrl+Z → 도형 복원·개수 원복
    page.keyboard.press("Control+z"); page.wait_for_timeout(120)
    nr2 = n_rooms(page)
    i_back = face_at(page, Ic) >= 0
    print(f"[B undo] 방 {nr1}→{nr2} I복원={i_back}")
    assert nr2 == nr0, f"undo 후 개수 원복 안 됨: {nr2}"
    assert i_back, "undo 후 고립 방 도형 복원 안 됨"

    # ── C. 인접쌍 한쪽(L) 삭제 → 옆 방(R) 면 유지(공유변 보존) ──
    r_area_before = page.evaluate("(()=>{const i=" + str(0) + ";return null;})()")  # placeholder noop
    ri2 = face_at(page, Rc)
    r_area_before = page.evaluate("designRooms[%d].area_m2" % ri2)
    click_mm(page, Lc)
    assert page.evaluate("selectedDesignFace") >= 0, "L 선택 실패"
    page.click("#btn-delete-room"); page.wait_for_timeout(120)
    nr3 = n_rooms(page)
    l_gone = face_at(page, Lc) < 0
    ri3 = face_at(page, Rc)
    r_area_after = page.evaluate("designRooms[%d].area_m2" % ri3) if ri3 >= 0 else None
    print(f"[C 인접삭제] 방 {nr2}→{nr3} L사라짐={l_gone} R유지={ri3>=0} R면적 {r_area_before}→{r_area_after}")
    assert nr3 == nr2 - 1, f"인접 삭제 1 감소 아님: {nr2}→{nr3}"
    assert l_gone, "삭제한 L이 안 사라짐"
    assert ri3 >= 0, "옆 방 R이 같이 깨짐(공유변 보존 실패)"
    assert abs(r_area_after - r_area_before) < 0.3, f"R 면적 변함(공유변 영향): {r_area_before}→{r_area_after}"
    page.keyboard.press("Control+z"); page.wait_for_timeout(120)
    assert n_rooms(page) == nr2, "C undo 복원 실패"

    # ── D. 잠긴 방: 삭제 버튼 disabled + Delete 무반응 ──
    page.click("#btn-fix-mode"); page.wait_for_timeout(60)   # edit→fix (상호배타)
    click_mm(page, Ic)
    page.fill("#design-fix-input", "현관")
    page.click("#btn-lock-fixed"); page.wait_for_timeout(80)
    locked = page.evaluate("designFixedRooms.length")
    print(f"[D 잠금] designFixedRooms={locked}")
    assert locked == 1, "고정 잠금 실패"
    page.click("#btn-fix-mode"); page.wait_for_timeout(60)   # fix 모드 종료
    page.click("#btn-edit-mode"); page.wait_for_timeout(60)
    click_mm(page, Ic)   # 잠긴 방 선택
    del_dis = page.evaluate("document.getElementById('btn-delete-room').disabled")
    nr_d = n_rooms(page)
    # Delete 키 (입력칸 밖)
    page.evaluate("document.body.focus()")
    page.keyboard.press("Delete"); page.wait_for_timeout(100)
    nr_d2 = n_rooms(page)
    print(f"[D 잠금삭제차단] 버튼disabled={del_dis} Delete후 방 {nr_d}→{nr_d2}")
    assert del_dis is True, "잠긴 방 삭제 버튼이 활성"
    assert nr_d2 == nr_d, "잠긴 방이 Delete로 삭제됨"

    # ── E. Delete 키 경로 삭제 / 입력 포커스 중 무반응 ──
    page.evaluate("unlockFixedRoom(0)"); page.wait_for_timeout(80)   # I 잠금 해제
    assert page.evaluate("designFixedRooms.length") == 0
    # 입력 포커스 중 Delete → 무반응
    click_mm(page, Lc)   # L 선택
    assert page.evaluate("selectedDesignFace") >= 0
    nr_e0 = n_rooms(page)
    page.focus("#edit-w-input")
    page.keyboard.press("Delete"); page.wait_for_timeout(100)
    nr_e1 = n_rooms(page)
    print(f"[E 입력포커스 가드] 방 {nr_e0}→{nr_e1} (불변이어야)")
    assert nr_e1 == nr_e0, "W 입력칸 포커스 중 Delete가 방을 삭제함(가드 실패)"
    # 입력 밖으로 포커스 이동(선택은 JS 변수라 유지됨) → Delete → 삭제
    page.evaluate("document.getElementById('edit-w-input').blur(); document.body.focus();")
    assert page.evaluate("selectedDesignFace") >= 0, "입력 blur 후 선택 유지 실패"
    nr_e2 = n_rooms(page)
    page.keyboard.press("Delete"); page.wait_for_timeout(120)
    nr_e3 = n_rooms(page)
    print(f"[E Delete키 삭제] 방 {nr_e2}→{nr_e3}")
    assert nr_e3 == nr_e2 - 1, f"Delete 키 경로 삭제 실패: {nr_e2}→{nr_e3}"

    # ── F. 완전히 둘러싸인 방(3×3 격자 중앙) 삭제 불가 (합성 로직 검증) ──
    spot2 = page.evaluate(FIND_RECT, {"w": 2400, "h": 2400})
    assert spot2, "2.4×2.4 격자 자리 못 찾음"
    GX, GY = spot2["a"]
    page.evaluate("""(g)=>{
      const X=g.x, Y=g.y, S=800;
      designHistory=[]; designWalls=[]; designRoomNames=[]; designFixedRooms=[]; selectedDesignFace=-1;
      for(let i=0;i<=3;i++){const xx=X+i*S; designWalls.push({a:[xx,Y],b:[xx,Y+3*S]});}
      for(let j=0;j<=3;j++){const yy=Y+j*S; designWalls.push({a:[X,yy],b:[X+3*S,yy]});}
      dedupDesignWalls(); recomputeDesignRooms(); renderDesignWalls(); renderDesignRooms();
    }""", {"x": GX, "y": GY})
    grid_n = n_rooms(page)
    center = [GX + 1200, GY + 1200]   # 중앙 셀 중심
    ci = face_at(page, center)
    print(f"[F 격자] 셀 {grid_n}개 중앙idx={ci}")
    assert grid_n == 9, f"3×3 격자 9칸 아님: {grid_n}"
    assert ci >= 0, "중앙 셀 매칭 실패"
    hist_before = page.evaluate("designHistory.length")
    page.evaluate("(i)=>{selectedDesignFace=i;}", ci)
    page.evaluate("deleteSelectedRoom()"); page.wait_for_timeout(100)
    grid_n2 = n_rooms(page)
    hist_after = page.evaluate("designHistory.length")
    print(f"[F 둘러싸임 차단] 방 {grid_n}→{grid_n2} history {hist_before}→{hist_after}")
    assert grid_n2 == grid_n, "완전 둘러싸인 방이 삭제됨(공유변 보존 실패)"
    assert hist_after == hist_before, "삭제 불가인데 history가 변함"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 방 삭제 검증 통과 (고립삭제·1감소·이름정리·인접보존·undo·잠금차단·Delete키·입력가드·둘러싸임차단·에러0)")
    browser.close()
