"""extractor 파이프라인 시각화 테스트 — 도면이미지.png 외곽 추출 검증"""
import cv2
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from extractor import extract_outline, _step3_ocr_tokens, _step0_crop_floorplan

IMG = os.path.join(os.path.dirname(__file__), "도면이미지.png")

buf = np.fromfile(IMG, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
if img is None:
    sys.exit("이미지 로드 실패")

print(f"원본 크기: {img.shape[1]}x{img.shape[0]}")

result = extract_outline(IMG)
print("\n=== 파이프라인 결과 ===")
print(f"꼭짓점 수  : {len(result.pts_px)}")
print(f"면적       : {result.area_m2} m²")
print(f"스케일     : {result.scale_mm_per_px:.4f} mm/px")
print(f"confidence : {result.confidence}")
print("warnings:")
for w in result.warnings:
    print(" ", w.encode("utf-8", errors="replace").decode("cp949", errors="replace"))

# 외곽선 원본 이미지에 그리기
vis = img.copy()
pts = result.pts_px
for i in range(len(pts)):
    cv2.line(vis, pts[i], pts[(i + 1) % len(pts)], (0, 0, 255), 3)
for p in pts:
    cv2.circle(vis, p, 6, (0, 255, 0), -1)

out_vis = os.path.join(os.path.dirname(__file__), "test_outline.png")
cv2.imwrite(out_vis, vis)
print(f"\n외곽선 시각화 저장: {out_vis}")
