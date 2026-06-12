"""extractor 전체 파이프라인 테스트 — 임의 도면 이미지 외곽 추출 검증

사용법:
    python test_pipeline.py [이미지경로]
    (인자 생략 시 도면이미지.png 사용)
"""
import cv2
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from extractor import extract_outline

IMG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "도면이미지.png")

if not os.path.exists(IMG):
    sys.exit(f"이미지 없음: {IMG}")

buf = np.fromfile(IMG, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
if img is None:
    sys.exit("이미지 로드 실패")

print(f"입력 이미지 : {IMG}")
print(f"원본 크기   : {img.shape[1]}x{img.shape[0]}")

result = extract_outline(IMG)
print("\n=== 파이프라인 결과 ===")
print(f"꼭짓점 수  : {len(result.pts_px)}")
print(f"면적       : {result.area_m2} m²")
print(f"스케일     : {result.scale_mm_per_px:.4f} mm/px")
print(f"confidence : {result.confidence}")
print("warnings:")
sys.stdout.reconfigure(encoding="utf-8")
for w in result.warnings:
    print(" ", w)

# 외곽선 원본 이미지에 그리기
vis = img.copy()
pts = result.pts_px
for i in range(len(pts)):
    cv2.line(vis, pts[i], pts[(i + 1) % len(pts)], (0, 0, 255), 3)
for i, p in enumerate(pts):
    cv2.circle(vis, p, 6, (0, 255, 0), -1)
    cv2.putText(vis, str(i), (p[0] + 8, p[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)

base = os.path.splitext(os.path.basename(IMG))[0]
# Windows 한글 경로 대응: ASCII 안전 이름 + imencode/tofile
safe = "".join(c if c.isascii() and c.isalnum() else "_" for c in base) or "out"
out_vis = os.path.join(os.path.dirname(__file__), f"test_outline_{safe}.png")
ok, enc = cv2.imencode(".png", vis)
if ok:
    enc.tofile(out_vis)
    print(f"\n외곽선 시각화 저장: {out_vis}")
else:
    print("\n시각화 인코딩 실패")
