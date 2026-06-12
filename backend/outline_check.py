"""
outline_check.py — pdf_parser 외곽 시각 검증

parse_pdf()가 뽑은 building_outline_mm 폴리곤을 원본 PDF 렌더 위에 정합시켜
반투명 겹침 이미지(outline_check.png)를 만든다. 외곽선이 실제 건물 벽과
일치하는지 한눈에 확인용.

사용법: python outline_check.py [PDF경로]  (기본 테스트용.pdf)
"""
import os
import sys

import pdfplumber
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
from pdf_parser import parse_pdf, _building_outline_pts_pt

sys.stdout.reconfigure(encoding="utf-8")

PDF = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "테스트용.pdf")
R = 150  # 렌더 해상도(dpi). px = pt × R/72

# 1) 파싱
r = parse_pdf(PDF)
outline_mm = r["building_outline_mm"]
s = r["scale_mm_per_pt"]

# 2) mm → pt 복원용 오프셋: 정규화 때 뺀 min_pt (= 외곽 pt 좌표의 최소값)
outline_pt = _building_outline_pts_pt(pdfplumber.open(PDF).pages[0])
min_x_pt = min(x for x, _ in outline_pt)
min_y_pt = min(y for _, y in outline_pt)

def mm_to_px(mx, my):
    px = (mx / s + min_x_pt) * R / 72.0
    py = (my / s + min_y_pt) * R / 72.0
    return px, py

# 3) 원본 PDF 렌더
page = pdfplumber.open(PDF).pages[0]
render = page.to_image(resolution=R).original.convert("RGB")
Wpx, Hpx = render.size

# 4) 폴리곤 레이어(연두 채움) → 그 위에 PDF를 반투명(65%)으로 겹침
poly_px = [mm_to_px(x, y) for x, y in outline_mm]
poly_layer = Image.new("RGB", (Wpx, Hpx), (255, 255, 255))
ImageDraw.Draw(poly_layer).polygon(poly_px, fill=(120, 220, 120))
blended = Image.blend(poly_layer, render, alpha=0.65)  # PDF 65% + 폴리곤 35%

# 5) 외곽선(빨강 굵게) + 꼭짓점 번호를 위에 불투명하게
d = ImageDraw.Draw(blended)
d.line(poly_px + [poly_px[0]], fill=(220, 0, 0), width=4)
for i, (px, py) in enumerate(poly_px):
    d.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(0, 0, 255))
    d.text((px + 8, py - 6), str(i), fill=(0, 0, 255))

# 6) 건물 영역으로 크롭(여백 8%)
xs = [p[0] for p in poly_px]; ys = [p[1] for p in poly_px]
mx = (max(xs) - min(xs)) * 0.08; my = (max(ys) - min(ys)) * 0.08
box = (max(0, int(min(xs) - mx)), max(0, int(min(ys) - my)),
       min(Wpx, int(max(xs) + mx)), min(Hpx, int(max(ys) + my)))
cropped = blended.crop(box)

out_path = os.path.join(os.path.dirname(__file__), "outline_check.png")
cropped.save(out_path)

# 7) 좌표·면적 출력
print(f"입력 PDF   : {PDF}")
print(f"스케일     : {s} mm/pt  (1/{int(r['scale_denominator'])})")
print(f"꼭짓점 수  : {len(outline_mm)}")
print(f"면적       : {r['outline_area_m2']} m²")
print("외곽 좌표(mm, 좌상단 0,0 기준):")
for i, (x, y) in enumerate(outline_mm):
    print(f"  v{i}: ({x:.1f}, {y:.1f})")
for w in r["warnings"]:
    print("  ⚠", w)
print(f"\n검증 이미지 저장: {out_path}")
