# extractor.py 설계 문서

## 핵심 원칙

```
Vision API  →  숫자 읽기만
코드        →  계산 + 검증 + 좌표 생성
```

Vision API에게 좌표 추정, 선 추적, 조합을 시키지 않는다.
도면에 적힌 치수 숫자를 받아쓰기만 시키고, 나머지는 전부 코드가 처리한다.

## Vision API 역할 (딱 이것만)

도면 4면의 치수선 숫자를 순서대로 반환:

```json
{
  "top_dims":      [3000, 3300, 3300, 3400],
  "right_dims":    [2600, 2600, 3300, 3400, 2700, 1800, 2900],
  "bottom_dims":   [3900, 3300, 2400, 3400],
  "left_dims":     [1300, 2700, 2700, 5200, 2700, 3300, 1400],
  "units": [
    {"name": "A", "area_m2": 59.76, "rooms": ["거실","침실","욕실","주방","현관","발코니"]},
    {"name": "B", "area_m2": 65.21, "rooms": ["거실","침실","침실","욕실","주방","현관","파우더룸"]},
    {"name": "C", "area_m2": 64.00, "rooms": ["거실","침실","침실","욕실","주방","현관","발코니"]}
  ],
  "common_areas":   ["계단실", "엘리베이터", "복도"],
  "common_area_m2": 18.41
}
```

Vision API 프롬프트 3대 원칙:
1. 도면 4면의 치수선 숫자를 순서대로 나열하라
2. 세대 이름, 면적, 방 이름만 읽어라
3. 계산, 조합, 검증은 하지 마라

## 코드 처리 로직

### Step 1 — 사선 계산 (Vision이 아닌 코드가 계산)
```python
diagonal_dx = sum(top_dims) - sum(bottom_dims)   # 예: 13000 - 9600 = 3400 → -3400
diagonal_dy = sum(left_dims) - sum(right_dims)    # 예: 19300 - 16400 = 2900
```

### Step 2 — 오각형 좌표 5개 생성 (항상 정확히 5개)
```python
P0 = (0, 0)
P1 = (sum(top_dims), 0)
P2 = (sum(top_dims), sum(right_dims))
P3 = (sum(top_dims) - diagonal_dx, sum(right_dims) + diagonal_dy)
P4 = (0, sum(left_dims))
```

역곡동 도면 예시:
```
P0 = (0, 0)
P1 = (13000, 0)
P2 = (13000, 16400)
P3 = (11200, 19300)
P4 = (0, 19300)
```

### Step 3 — 검증 (불일치 시 경고만, 서비스 중단 안 함)
```python
가로: sum(top_dims) == sum(bottom_dims) + abs(diagonal_dx)  ±50mm
세로: sum(left_dims) == sum(right_dims) + diagonal_dy       ±50mm
면적: shoelace(pts_mm) ≈ sum(unit areas) + common_area_m2  ±10%
```

## 세대 규칙
- 반드시 3개 (A, B, C)
- 공용부(계단실/엘리베이터/복도)는 절대 세대로 분류 금지
- 18.41m² 공용부는 common_areas로만 처리

## OpenCV Fallback
ANTHROPIC_API_KEY 없거나 Vision API 실패 시:
- OpenCV로 외곽선 추출 (대략적)
- Tesseract OCR로 치수 숫자 읽기 시도
- confidence: 0.4 (낮음 표시)

## 한계
- Vision이 숫자 자체를 잘못 읽으면 (3300 → 3800) 오류 발생
- 치수선이 없는 도면은 처리 불가
- 복잡한 ㄱ자/L자 건물은 추가 로직 필요

## 변경 이력
- 2026.06.10: OpenCV 픽셀 추출 방식으로 시작
- 2026.06.10: Claude Vision API 통합 (픽셀 좌표 추정 방식)
- 2026.06.11: Vision API 역할 재설계 — 좌표 추정 → 치수 읽기만
- 2026.06.11: 코드가 사선/좌표/검증 전담하는 구조로 확정
