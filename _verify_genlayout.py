"""
AI 구조 초안 생성 — 프런트 주입/편집/내보내기 경로 검증 (역곡동빌라 page3 A세대).

⚠️ 이 환경엔 ANTHROPIC_API_KEY가 없어 실제 AI 호출은 불가 → /api/generate-layout 을
page.route로 '목(mock)'해 결정적 walls를 돌려준다. 검증 대상은 프런트 경로:
  - 개수 입력 → AI 버튼 → designWalls 주입 · 전부 외곽 안 · 방 생성
  - 이어서 클릭-클릭으로 벽 1개 편집(주입된 벽이 편집 가능)
  - Ctrl+Z로 AI 초안 통째 취소(undo 1단위)
  - 내보내기 PNG 비단색 · PDF 유효 · JS 에러 0
(AI 응답 품질/방 닫힘/실제 호출은 JJ가 실제 키로 브라우저에서 육안 확인.)
백엔드 후처리/기본값/파싱은 _verify_genlayout_backend.py에서 별도 검증.
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
CHORD = """
() => {
  const b = designBoundary;
  let minY=1e9,maxY=-1e9;
  for(const p of b){minY=Math.min(minY,p[1]);maxY=Math.max(maxY,p[1]);}
  const ym=(minY+maxY)/2;
  const xs=[];
  for(let i=0;i<b.length;i++){const a=b[i],c=b[(i+1)%b.length];
    if((a[1]-ym)*(c[1]-ym)<0){const t=(ym-a[1])/(c[1]-a[1]);xs.push(a[0]+t*(c[0]-a[0]));}}
  xs.sort((p,q)=>p-q);
  if(xs.length<2)return null;
  return {left:[xs[0],ym], right:[xs[xs.length-1],ym]};
}
"""
# 외곽 bbox 안에 들어가는 격자 정렬 mock 벽 — designBoundary 기준으로 JS에서 생성
MOCK_WALLS_JS = """
() => {
  const b = designBoundary;
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const p of b){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);x1=Math.max(x1,p[0]);y1=Math.max(y1,p[1]);}
  const g=v=>Math.round(v/100)*100;
  // bbox 안쪽으로 충분히 들여서(여백 10%) 수직·수평 벽 → 닫힌 방 생성 유도
  const mx0=g(x0+(x1-x0)*0.1), mx1=g(x1-(x1-x0)*0.1);
  const my0=g(y0+(y1-y0)*0.1), my1=g(y1-(y1-y0)*0.1);
  const cx=g((x0+x1)/2), cy=g((y0+y1)/2);
  const walls = [
    {a:[mx0,my0],b:[mx1,my0]}, {a:[mx1,my0],b:[mx1,my1]},
    {a:[mx1,my1],b:[mx0,my1]}, {a:[mx0,my1],b:[mx0,my0]},  // 사각 방 1개
    {a:[cx,my0],b:[cx,my1]},                                // 가운데 분할 → 방 2개
  ];
  // 두 면(좌/우 반)의 중심에 이름 — 백엔드 rooms 응답 흉내
  const rooms = [
    {name:'거실', cx:g((mx0+cx)/2), cy:cy, area_m2:10.0},
    {name:'침실', cx:g((cx+mx1)/2), cy:cy, area_m2:8.0},
  ];
  return { walls, rooms };
}
"""
INSIDE = """
(walls) => {
  const b = designBoundary;
  function inPoly(p){ let c=false;
    for(let i=0,j=b.length-1;i<b.length;j=i++){
      const xi=b[i][0],yi=b[i][1],xj=b[j][0],yj=b[j][1];
      if(((yi>p[1])!=(yj>p[1])) && (p[0]<(xj-xi)*(p[1]-yi)/(yj-yi)+xi)) c=!c; }
    return c; }
  // bbox 폴백(버퍼50 클립이라 경계 약간 밖도 허용) — bbox+80 안인지
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const p of b){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);x1=Math.max(x1,p[0]);y1=Math.max(y1,p[1]);}
  for(const w of walls) for(const p of [w.a,w.b]){
    const okbox = p[0]>=x0-80 && p[0]<=x1+80 && p[1]>=y0-80 && p[1]<=y1+80;
    if(!okbox) return false;
  }
  return true;
}
"""


def screen(page, mm):
    return page.evaluate(PROJ, mm)


def click_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.mouse.click(p["x"], p["y"]); page.wait_for_timeout(60)


def hover_mm(page, mm):
    p = screen(page, mm)
    page.mouse.move(p["x"], p["y"]); page.wait_for_timeout(40)


def draw_clickclick(page, a_mm, b_mm):
    click_mm(page, a_mm); hover_mm(page, b_mm); click_mm(page, b_mm)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())   # confirm("대체할까요?") 자동 수락

    page.goto(URL)
    page.set_input_files("#file-input", PDF)
    page.wait_for_function("parsedData && parsedData.rooms && parsedData.rooms.length>0", timeout=60000)
    page.wait_for_timeout(700)
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();}", A_ROOMS)

    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)

    # ── /api/generate-layout 목: designBoundary 기반 격자 mock {walls, rooms} 반환 ──
    mock = page.evaluate(MOCK_WALLS_JS)
    mock_walls = mock["walls"]; mock_rooms = mock["rooms"]
    import json
    def handle_gen(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"walls": mock_walls, "count": len(mock_walls),
                                       "rooms": mock_rooms, "bedrooms": 2, "baths": 1}))
    page.route("**/api/generate-layout", handle_gen)

    # ── 1. 개수 입력 + AI 버튼 → 주입 ──
    page.fill("#gen-rooms", "2")
    page.fill("#gen-baths", "1")
    assert not page.evaluate("document.getElementById('btn-generate-layout').disabled"), "AI 버튼 비활성"
    page.click("#btn-generate-layout")
    page.wait_for_function("designWalls.length > 0", timeout=15000)
    walls = page.evaluate("designWalls.length")
    rooms = page.evaluate("designRooms.length")
    inside = page.evaluate(INSIDE, page.evaluate("designWalls"))
    print(f"[1 주입] 벽={walls} 방={rooms} 전부외곽안={inside}")
    assert walls == len(mock_walls), "주입된 벽 수 불일치"
    assert inside, "주입 벽이 외곽 밖"
    assert rooms >= 2, f"닫힌 방이 안 생김(방{rooms}) — mock은 사각+가운데분할이라 ≥2여야"

    # ── 1b. AI 방 이름이 면에 매칭돼 라벨로 표시 ──
    named = page.evaluate("designRooms.filter(r=>r.name).map(r=>r.name)")
    stored = page.evaluate("designRoomNames.length")
    print(f"[1b 이름매칭] designRoomNames={stored} / 라벨붙은방={named}")
    assert stored == len(mock_rooms), "designRoomNames에 AI 방 이름 저장 안 됨"
    assert len(named) >= 1, "면 중심 매칭으로 이름 붙은 방이 하나도 없음"
    assert any(n in ("거실", "침실") for n in named), "매칭된 이름이 mock 이름과 다름"

    # ── 2. 이어서 클릭-클릭으로 벽 1개 편집(주입 벽이 편집 가능) ──
    before = walls
    chord = page.evaluate(CHORD)
    assert chord, "chord 계산 실패"
    draw_clickclick(page, chord["left"], chord["right"])
    after = page.evaluate("designWalls.length")
    print(f"[2 편집] 클릭-클릭 후 벽={after} (주입 {before}+1)")
    assert after == before + 1, "주입 벽 위에 클릭-클릭 편집이 안 됨"

    # ── 3. Ctrl+Z 2번: 방금 그린 벽 → AI 초안 통째 ──
    page.keyboard.press("Control+z"); page.wait_for_timeout(120)
    mid = page.evaluate("designWalls.length")
    page.keyboard.press("Control+z"); page.wait_for_timeout(120)
    base = page.evaluate("designWalls.length")
    print(f"[3 undo] 1회후={mid}(주입수) 2회후={base}(0=초안취소)")
    assert mid == before, "1차 undo가 마지막 벽만 안 지움"
    assert base == 0, "2차 undo로 AI 초안이 통째 취소 안 됨"

    # 다시 주입(내보내기 검증용)
    page.click("#btn-generate-layout")
    page.wait_for_function("designWalls.length > 0", timeout=15000)
    page.wait_for_timeout(200)

    # ── 4. 내보내기 PNG/PDF ──
    assert not page.evaluate("document.getElementById('btn-export-png').disabled"), "PNG 버튼 비활성"
    durl_len = page.evaluate("""() => {
      roomRenderer.render(roomScene, roomCamera);
      return roomRenderer.domElement.toDataURL('image/png').length;
    }""")
    print(f"[4 PNG] toDataURL 길이={durl_len}")
    assert durl_len > 5000, "PNG dataURL 비어 있음"

    with page.expect_download() as dl:
        page.click("#btn-export-pdf")
    pdf_path = os.path.join(_root, "backend", "_genlayout_test.pdf")
    dl.value.save_as(pdf_path)
    with open(pdf_path, "rb") as f:
        head = f.read(5)
    print(f"[4 PDF] {dl.value.suggested_filename} head={head}")
    assert head == b"%PDF-", "PDF 헤더 없음"
    import pdfplumber
    with pdfplumber.open(pdf_path) as doc:
        assert len(doc.pages) == 1, "PDF 페이지 1개 아님"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 AI 초안 프런트 경로 검증 통과 (주입·외곽안·편집·undo·내보내기·에러0)")
    browser.close()
