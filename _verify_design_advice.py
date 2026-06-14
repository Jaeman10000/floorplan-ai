"""
설계 시작 조언 프런트 경로 검증 — 역곡동빌라 page3 A세대.
/api/design-advice를 page.route로 '목'해 결정적 응답을 돌려준다. 검증:
1. 💡 버튼이 designBoundary 있을 때 활성.
2. 버튼 클릭 → 요청 body에 boundary·orientation·fixed_rooms·bedrooms/baths·current_rooms·trend 실림.
3. 목 응답 텍스트가 design-advice-response 패널에 렌더.
4. 조언 요청해도 designWalls 불변(벽 생성 안 함).
5. 트렌드 빈칸도 동작.
6. 직사각형 방 하나 그린 뒤 다시 조언 → current_rooms에 그린 방 실림.
7. 페이지 에러 0.
"""
import sys, glob, os, json
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
FIND_RECT = """
() => {
  const b=designBoundary; let a=1e9,c=1e9,d=-1e9,e=-1e9;
  for(const p of b){a=Math.min(a,p[0]);d=Math.max(d,p[0]);c=Math.min(c,p[1]);e=Math.max(e,p[1]);}
  function dB(pt){let m=1e12;for(let i=0;i<b.length;i++){const A=b[i],C=b[(i+1)%b.length];
    const vx=C[0]-A[0],vy=C[1]-A[1],L=vx*vx+vy*vy;let t=L>1e-6?((pt[0]-A[0])*vx+(pt[1]-A[1])*vy)/L:0;t=Math.max(0,Math.min(1,t));
    m=Math.min(m,Math.hypot(pt[0]-(A[0]+t*vx),pt[1]-(A[1]+t*vy)));}return m;}
  const R=v=>Math.round(v/100)*100;
  for(let h=2400;h>=1500;h-=200)for(let w=2000;w>=1400;w-=200)
   for(let x=R(a)+200;x<=d-w-200;x+=300)for(let y=R(c)+200;y<=e-h-200;y+=300){
     const X=R(x),Y=R(y),W=R(w),H=R(h);const pts=[[X,Y],[X+W,Y],[X+W,Y+H],[X,Y+H]];
     if(pts.every(p=>pointInPoly(p,b)&&dB(p)>450))return{a:[X,Y],b:[X+W,Y+H]};}
  return null;
}
"""

captured = []   # 목으로 가로챈 요청 body들


def handle_advice(route):
    req = route.request
    try:
        captured.append(json.loads(req.post_data))
    except Exception:
        captured.append(None)
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"answer": "• 남향 외벽을 따라 거실을 길게 배치하세요.\n• 북쪽에 욕실·주방을 모아 배관을 집중하세요.",
                                   "context": "mock"}))


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"])
    page.mouse.click(p["x"], p["y"])
    page.wait_for_timeout(60)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.route("**/api/design-advice", handle_advice)

    page.goto(URL)
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();}", A_ROOMS)

    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)

    # ── 1. 버튼 활성 ──
    disabled = page.evaluate("document.getElementById('btn-advice').disabled")
    print(f"[1 버튼] disabled={disabled} (False여야)")
    assert disabled is False, "designBoundary 있는데 조언 버튼 비활성"

    # ── 2/3/4/5. 방위·개수·트렌드 빈칸으로 조언 요청 ──
    page.select_option("#design-orientation-select", "남")
    page.fill("#gen-rooms", "3")
    page.fill("#gen-baths", "1")
    walls_before = page.evaluate("designWalls.length")
    page.click("#btn-advice")
    page.wait_for_function("document.getElementById('advice-response').textContent.includes('남향')", timeout=15000)
    body1 = captured[-1]
    rendered = page.evaluate("document.getElementById('advice-response').textContent")
    walls_after = page.evaluate("designWalls.length")
    print(f"[2 body] keys={sorted(body1.keys())}")
    print(f"   orientation={body1.get('building_orientation')} bedrooms={body1.get('bedrooms')} baths={body1.get('baths')} trend={body1.get('trend')!r}")
    print(f"   boundary_len={len(body1.get('boundary_mm') or [])} fixed={len(body1.get('fixed_rooms') or [])} current={len(body1.get('current_rooms') or [])}")
    print(f"[3 렌더] {rendered[:40]}...")
    print(f"[4 벽 불변] {walls_before}→{walls_after}")
    assert body1.get("building_orientation") == "남", "방위 안 실림"
    assert body1.get("bedrooms") == 3 and body1.get("baths") == 1, "개수 안 실림"
    assert len(body1.get("boundary_mm") or []) >= 3, "boundary 안 실림"
    assert "fixed_rooms" in body1 and "current_rooms" in body1, "fixed/current 키 없음"
    assert body1.get("trend") == "", "빈 트렌드 처리 실패"
    assert "남향" in rendered, "응답 텍스트 렌더 실패"
    assert walls_before == walls_after == 0, "조언 요청이 벽을 생성함"

    # ── 6. 직사각형 방 하나 그린 뒤 다시 조언 → current_rooms 반영 ──
    r = page.evaluate(FIND_RECT)
    assert r, "외곽 안 직사각형 못 찾음"
    page.click("#btn-rect-mode")
    click_mm(page, r["a"])
    p = screen(page, r["b"]); page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(40)
    click_mm(page, r["b"])
    assert page.evaluate("designRooms.length") >= 1, "직사각형 방 생성 실패"
    page.click("#btn-rect-mode")   # 모드 끄기
    # 트렌드 입력해서 다시 조언
    page.fill("#design-trend-input", "요즘 4베이 선호")
    n_before = len(captured)
    page.click("#btn-advice")
    page.wait_for_timeout(900)
    assert len(captured) > n_before, "두 번째 조언 요청이 안 나감"
    body2 = captured[-1]
    print(f"[6 재요청] current_rooms={len(body2.get('current_rooms') or [])} trend={body2.get('trend')!r}")
    assert len(body2.get("current_rooms") or []) >= 1, "그린 방이 current_rooms에 안 실림"
    assert body2.get("trend") == "요즘 4베이 선호", "트렌드 입력 안 실림"
    cr = body2["current_rooms"][0]
    assert "area_m2" in cr and "cx" in cr and "cy" in cr, "current_rooms 필드 부족"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 설계 시작 조언 프런트 경로 검증 통과 (버튼·body·렌더·벽불변·트렌드·현재방·에러0)")
    browser.close()
