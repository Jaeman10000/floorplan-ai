# extractor.py 설계 문서

## 핵심 원칙

```
Vision API  →  외벽 픽셀 좌표 직접 추적 + 치수선으로 스케일 계산
코드        →  pts_mm = pts_px × scale_mm_per_px
```

치수선 숫자는 **스케일 계산에만** 사용한다. 좌표 계산에 절대 사용하지 않는다.

## Vision API 역할

### 할 일 1: 외벽 꼭짓점 픽셀 좌표 추적

1. 건물 최외곽 벽체선(가장 굵은 이중선)을 찾는다
2. 좌상단 꼭짓점부터 시계방향으로 모든 꼭짓점을 추적한다
3. 각 꼭짓점의 픽셀 좌표 `[x, y]`를 읽는다 (이미지 좌상단이 `[0, 0]`)
4. 꼭짓점은 벽선이 꺾이는 지점만 — 직선 중간점 포함 금지
5. 세대 구분선·대지 경계선이 아닌 건물 외벽만

### 할 일 2: 스케일 계산

6. 치수선(화살표+숫자) 하나를 찾는다 (긴 것 우선)
7. 치수선 양 끝 픽셀 거리를 측정한다
8. `scale_mm_per_px = 치수_숫자(mm) / 픽셀_거리`

### 응답 JSON

```json
{
  "pts_px": [[120, 85], [780, 85], [780, 620], [650, 750], [120, 750]],
  "scale_mm_per_px": 18.5,
  "scale_ref": {"dim_mm": 13000, "px_dist": 703},
  "units": [
    {"name": "A", "area_m2": 59.76, "rooms": ["거실", "침실", "욕실", "주방"]},
    {"name": "B", "area_m2": 65.21, "rooms": ["거실", "침실", "침실", "욕실", "주방"]},
    {"name": "C", "area_m2": 64.00, "rooms": ["거실", "침실", "침실", "욕실", "주방"]}
  ],
  "common_areas": ["계단실", "엘리베이터홀"],
  "common_area_m2": 18.41,
  "confidence": 0.85,
  "warnings": []
}
```

## 코드 처리 로직

```python
pts_px = data["pts_px"]                        # Vision이 읽은 픽셀 좌표
scale  = data["scale_mm_per_px"]               # Vision이 계산한 스케일
pts_mm = [(x * scale, y * scale) for x, y in pts_px]
area_m2 = shoelace(pts_mm) / 1e6
```

별도 좌표 계산 없음. 폐합 검증도 없음 — 픽셀 좌표가 직접 다각형을 구성한다.

## 구 방식과의 차이

| 항목 | 구 방식 (outline 배열) | 신 방식 (픽셀 좌표 추적) |
|------|----------------------|------------------------|
| Vision 입력 | 치수선 숫자 목록 | 외벽 픽셀 좌표 목록 |
| 오류 원인 | 합계/개별 치수 혼동 | 픽셀 위치 오인식 |
| 코드 계산 | outline_to_polygon() | pts_px × scale |
| 폐합 검증 | 필요 | 불필요 |
| 사선 처리 | dx/dy 수동 입력 | 자동 (픽셀 좌표로 표현됨) |

## 세대 규칙

- 반드시 3개 (A, B, C)
- 면적 30m² 미만은 세대 분류 금지
- 18m² 이하는 절대 세대(units)에 포함 금지
- 공용부(계단실/엘리베이터/복도)는 common_areas로만 처리

## OpenCV Fallback

ANTHROPIC_API_KEY 없거나 Vision API가 유효한 결과를 반환하지 못할 때:

- Vision이 `None`을 반환하는 조건: `pts_px` 없음, `scale_mm_per_px <= 0`, 꼭짓점 3개 미만
- OpenCV로 외곽선 추출 (대략적)
- Tesseract OCR로 치수 숫자 읽어 스케일 추정
- confidence: 0.4 (낮음 표시)

## 한계

- Vision이 외벽을 세대 구분선으로 오인하면 잘못된 다각형 생성
- 저해상도 이미지에서 픽셀 위치 오차 발생 가능
- 치수선이 없는 도면은 스케일 계산 불가 (OpenCV fallback)

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
| 2026.06.12 | 픽셀 좌표 직접 추적 방식으로 재설계 |
|            | outline 배열·outline_to_polygon() 완전 제거 |
|            | Vision: 치수 읽기 → 외벽 픽셀 좌표 추적 + 스케일 계산 |
|            | pts_mm = pts_px × scale_mm_per_px (코드 계산 단순화) |
