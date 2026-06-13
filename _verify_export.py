"""
내보내기(3D PNG / 2D 평면도 PDF) 검증 — 역곡동빌라 page3 A세대.

1. PDF 업로드 → A세대 지정 → 설계 모드 진입(A 자동선택) → 수평 벽 1개 드래그.
2. 3D PNG: 버튼 클릭 → 다운로드 → 파일이 빈(단색) 이미지가 아님(픽셀 분산>0).
3. 2D PDF: 버튼 클릭 → 다운로드 → %PDF 헤더 + pdfplumber 페이지 1개.
4. 페이지 에러 0.
"""
import sys, io, glob, os
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
# 한글 파일명 하드코딩 회피 — 빌라 PDF를 디렉터리에서 찾는다
_root = os.path.dirname(os.path.abspath(__file__))
_cands = [p for p in glob.glob(os.path.join(_root, "backend", "*.pdf"))
          if ("빌라" in p or "역곡" in p)]   # 빌라/역곡 (escape로 인코딩 무관)
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


def draw_chord(page, frac=1.0):
    """클릭-클릭으로 수평 벽 1개: 시작점 클릭 → hover → 끝점 클릭."""
    chord = page.evaluate(CHORD)
    assert chord, "chord 계산 실패"
    pL = page.evaluate(PROJ, chord["left"])
    pR = page.evaluate(PROJ, chord["right"])
    ex = pL["x"] + (pR["x"] - pL["x"]) * frac
    ey = pL["y"] + (pR["y"] - pL["y"]) * frac
    page.mouse.move(pL["x"], pL["y"]); page.mouse.click(pL["x"], pL["y"]); page.wait_for_timeout(60)
    page.mouse.move(ex, ey); page.wait_for_timeout(40)
    page.mouse.click(ex, ey); page.wait_for_timeout(160)


def png_has_variation(path):
    """단색(검은) 화면이 아닌지 — 채널 표준편차 합으로 확인."""
    from PIL import Image, ImageStat
    im = Image.open(path).convert("RGB")
    stat = ImageStat.Stat(im)
    stddev = sum(stat.stddev)
    return stddev, im.size


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

    # A세대 지정
    page.evaluate("(r)=>{r.forEach(i=>{parsedData.rooms[i].unit='A';applyRoomBaseColor(i);});updateUnitCounts();}", A_ROOMS)

    # 설계 모드 진입(A 자동) + 벽 1개
    page.click("#btn-design-mode")
    page.wait_for_function("designBoundary && designGroup && designUnit==='A'", timeout=20000)
    draw_chord(page)
    walls = page.evaluate("designWalls.length")
    rooms = page.evaluate("designRooms.length")
    print(f"[설계] A세대 벽={walls} 방={rooms}")
    assert walls >= 1 and rooms >= 2, f"그리기 실패: 벽{walls} 방{rooms}"

    # 버튼 활성화 확인
    png_enabled = page.evaluate("!document.getElementById('btn-export-png').disabled")
    pdf_enabled = page.evaluate("!document.getElementById('btn-export-pdf').disabled")
    print(f"[버튼] PNG enabled={png_enabled} / PDF enabled={pdf_enabled}")
    assert png_enabled and pdf_enabled, "내보내기 버튼 비활성"

    # ── 3D PNG ──
    # (a) 직접 렌더 후 dataURL 비어있지 않음
    durl_len = page.evaluate("""() => {
      roomRenderer.render(roomScene, roomCamera);
      return roomRenderer.domElement.toDataURL('image/png').length;
    }""")
    print(f"[PNG] toDataURL 길이={durl_len}")
    assert durl_len > 5000, "PNG dataURL이 비어 있음"

    # (b) 버튼 클릭 → 실제 다운로드 → 파일이 단색이 아님
    with page.expect_download() as dl_info:
        page.click("#btn-export-png")
    dl = dl_info.value
    png_path = r"C:\Users\jeffg\Documents\GitHub\floorplan-ai\backend\_export_test.png"
    dl.save_as(png_path)
    stddev, size = png_has_variation(png_path)
    print(f"[PNG 파일] {dl.suggested_filename} size={size} 색분산(stddev합)={stddev:.1f}")
    assert dl.suggested_filename.startswith("A_") and dl.suggested_filename.endswith(".png"), "PNG 파일명 규칙 불일치"
    assert stddev > 5.0, "PNG가 단색(빈/검은 화면)으로 보임"

    # ── 2D PDF ──
    with page.expect_download() as dl_info2:
        page.click("#btn-export-pdf")
    dl2 = dl_info2.value
    pdf_path = r"C:\Users\jeffg\Documents\GitHub\floorplan-ai\backend\_export_test.pdf"
    dl2.save_as(pdf_path)
    with open(pdf_path, "rb") as f:
        head = f.read(5)
    print(f"[PDF 파일] {dl2.suggested_filename} head={head}")
    assert dl2.suggested_filename.startswith("A_") and dl2.suggested_filename.endswith(".pdf"), "PDF 파일명 규칙 불일치"
    assert head == b"%PDF-", "PDF 헤더 없음"
    import pdfplumber
    with pdfplumber.open(pdf_path) as doc:
        print(f"[PDF] 페이지수={len(doc.pages)}")
        assert len(doc.pages) == 1, "PDF 페이지 1개 아님"

    print("page errors:", errors if errors else "없음")
    assert not errors, f"페이지 에러: {errors}"
    print("🎉 내보내기 검증 통과 (PNG 비단색 / PDF 유효 / 파일명 규칙 / 에러 0)")
    browser.close()
