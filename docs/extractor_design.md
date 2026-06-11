# extractor.py 설계 문서

## 핵심 원칙

```
Vision API  →  숫자 읽기만 (계산 금지)
코드        →  계산 + 검증 + 좌표 생성
```

## Vision API 역할 (딱 이것만)

상단·좌측 치수선 숫자 + 사선 여부만 반환한다.
`right_dims`, `bottom_dims`는 반환하지 않는다.

```json
{
  "top_dims":  [3000, 3300, 3300, 3400],
  "left_dims": [1300, 2700, 2700, 5200, 2700, 3300, 1400],
  "has_diagonal": true,
  "diagonal_horizontal_mm": 3400,
  "diagonal_vertical_mm":   2900,
  "units": [
    {"name": "A", "area_m2": 59.76, "rooms": ["거실","침실","욕실","주방","현관"]},
    {"name": "B", "area_m2": 65.21, "rooms": ["거실","침실","침실","욕실","주방","파우더룸"]},
    {"name": "C", "area_m2": 64.00, "rooms": ["거실","침실","침실","욕실","주방","발코니"]}
  ],
  "common_areas":   ["계단실", "엘리베이터홀"],
  "common_area_m2": 18.41,
  "confidence": 0.85,
  "warnings": []
}
```

Vision API 프롬프트 규칙:
1. 상단(top_dims)과 좌측(left_dims) 치수선만 읽는다
2. 세대 이름, 면적, 방 이름만 읽는다
3. 계산, 조합, 검증은 하지 않는다
4. right_dims·bottom_dims는 반환하지 않는다

## 코드 처리 로직 — build_pentagon()

### has_diagonal=false → 직사각형 4꼭짓점

```python
W = sum(top_dims)   # 건물 가로
H = sum(left_dims)  # 건물 세로

P0 = (0, 0)
P1 = (W, 0)
P2 = (W, H)
P3 = (0, H)
```

### has_diagonal=true → 우하단 삼각형 잘린 오각형 5꼭짓점

```python
W = sum(top_dims)
H = sum(left_dims)
diag_dx = diagonal_horizontal_mm  # 사선 가로 길이 (우→좌)
diag_dy = diagonal_vertical_mm    # 사선 세로 길이 (하→상)

P0 = (0,          0)
P1 = (W,          0)
P2 = (W,          H - diag_dy)   # 우측 벽 끝 (사선 시작)
P3 = (W - diag_dx, H)            # 사선 끝 (하단 벽 시작)
P4 = (0,          H)
```

역곡동 도면 예시 (top=13000, left=19300, dx=3400, dy=2900):
```
P0 = (0,     0)
P1 = (13000, 0)
P2 = (13000, 16400)   ← 19300 - 2900
P3 = (9600,  19300)   ← 13000 - 3400
P4 = (0,     19300)
```

## 세대 규칙

- 반드시 3개 (A, B, C)
- 면적 30m² 미만은 세대 분류 금지
- 18m² 이하는 절대 세대(units)에 포함 금지
- 공용부(계단실/엘리베이터/복도)는 common_areas로만 처리

## OpenCV Fallback

ANTHROPIC_API_KEY 없거나 Vision API 실패 시:
- OpenCV로 외곽선 추출 (대략적)
- Tesseract OCR로 치수 숫자 읽기 시도
- confidence: 0.4 (낮음 표시)

## 한계

- Vision이 숫자를 잘못 읽으면 오류 (3300 → 3800 등)
- 치수선이 없는 도면은 처리 불가
- 복잡한 ㄱ자/L자 건물은 추가 로직 필요
- 사선이 여러 개인 건물 미지원

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026.06.10 | OpenCV 픽셀 추출 방식으로 시작 |
| 2026.06.10 | Claude Vision API 통합 (픽셀 좌표 추정) |
| 2026.06.11 | Vision 역할 재설계 — 좌표 추정 → 치수 읽기만 |
| 2026.06.11 | 4면 치수 → build_pentagon() 구조 확정 |
| 2026.06.11 | top_dims+left_dims+has_diagonal 구조로 단순화 (right/bottom 제거) |
