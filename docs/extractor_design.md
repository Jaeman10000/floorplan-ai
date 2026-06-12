# extractor.py 설계 문서

## 핵심 원칙

```
Vision API  →  숫자 읽기만 (계산 금지)
코드        →  계산 + 검증 + 좌표 생성
```

## Vision API 역할 (딱 이것만)

건물 외곽을 **좌상단에서 시계방향으로 한 바퀴** 돌면서 각 변의 방향과 치수를 순서대로 읽는다.

```json
{
  "outline": [
    {"direction": "right",    "length_mm": 13000},
    {"direction": "down",     "length_mm": 16400},
    {"direction": "diagonal", "dx": -3400, "dy": 2900},
    {"direction": "left",     "length_mm": 9600},
    {"direction": "up",       "length_mm": 19300}
  ],
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

### 선분 형식

| 변 종류 | 형식 |
|---------|------|
| 직선    | `{"direction": "right"\|"left"\|"up"\|"down", "length_mm": 정수}` |
| 사선    | `{"direction": "diagonal", "dx": 정수, "dy": 정수}` |

- `dx` 양수=오른쪽, 음수=왼쪽
- `dy` 양수=아래, 음수=위

### Vision API 프롬프트 규칙

1. 좌상단 꼭짓점에서 시작, 시계방향으로 진행
2. 모든 변 빠짐없이 기재 — 마지막 변 끝이 시작점으로 정확히 돌아와야 함
3. 건물 외벽 치수선만 (대지 경계선·세대 구분선 무시)
4. 치수선 숫자 그대로 (계산·보정 금지)
5. 방 면적(소수) 을 length_mm에 넣지 말 것

## 코드 처리 로직 — outline_to_polygon()

```python
def outline_to_polygon(outline):
    x, y = 0, 0
    pts = [(x, y)]
    for seg in outline:
        d = seg["direction"]
        if d == "right":      x += seg["length_mm"]
        elif d == "left":     x -= seg["length_mm"]
        elif d == "down":     y += seg["length_mm"]
        elif d == "up":       y -= seg["length_mm"]
        elif d == "diagonal": x += seg["dx"]; y += seg["dy"]
        pts.append((x, y))
    return pts[:-1]   # 시작점 복귀점 제거
```

### 폐합 검증

마지막 점이 (0, 0)으로 돌아오는지 확인한다:
- `closure_error_mm > 100` → 경고: outline 치수 누락 의심
- `closure_error_mm > 10`  → 경고: 허용 범위 내 오차

### 지원하는 건물 형태 예시

**직사각형 (4꼭짓점)**
```
right 10000 → down 8000 → left 10000 → up 8000
P0=(0,0)  P1=(10000,0)  P2=(10000,8000)  P3=(0,8000)
```

**오각형 — 우하단 사선 (5꼭짓점, 역곡동 도면)**
```
right 13000 → down 16400 → diagonal(-3400,+2900) → left 9600 → up 19300
P0=(0,0)  P1=(13000,0)  P2=(13000,16400)  P3=(9600,19300)  P4=(0,19300)
```

**육각형 — 복수 사선 (6꼭짓점)**
```
right 13000 → down 16400
  → diagonal(-3400,+2900) → diagonal(-6200,0) → diagonal(-3400,-2900)
  → up 16400
```

**L자형 (6꼭짓점)**
```
right 8000 → down 5000 → left 4000 → down 5000 → left 4000 → up 10000
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
- 폐합 오차가 크면 외곽선 왜곡 가능

## 변경 이력

| 날짜       | 내용 |
|------------|------|
| 2026.06.10 | OpenCV 픽셀 추출 방식으로 시작 |
| 2026.06.10 | Claude Vision API 통합 (픽셀 좌표 추정) |
| 2026.06.11 | Vision 역할 재설계 — 좌표 추정 → 치수 읽기만 |
| 2026.06.11 | 4면 치수 → build_pentagon() 구조 확정 |
| 2026.06.11 | top_dims+left_dims+has_diagonal 구조로 단순화 |
| 2026.06.11 | building_height_mm 추가 — left_dims 합계 검증 + 자동 보정 |
| 2026.06.12 | outline 배열 구조로 완전 재설계 — 어떤 형태든 지원 |
|            | build_pentagon() 제거 → outline_to_polygon() 도입 |
|            | top_dims/left_dims/has_diagonal 완전 제거 |
