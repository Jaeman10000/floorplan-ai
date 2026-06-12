# floorplan-ai 프로젝트 컨텍스트

## 프로젝트 개요
AI 도면 분석 3D 공간 재설계 플랫폼 (SaaS)
- 도면 이미지 업로드 → 도면 분석 → DXF 변환 → Blender 3D → 웹 뷰어
- GitHub: Jaeman10000/floorplan-ai
- Railway 배포: floorplan-ai-production-c244.up.railway.app

## ⚠️ 핵심 설계 원칙 — extractor.py

### 각 도구의 역할 (절대 혼용 금지)

| 도구 | 잘하는 것 | 역할 |
|------|---------|------|
| 전용 딥러닝 모델 (CubiCasa5K / DeepFloorplan) | 벽 픽셀 감지 | 외곽/벽체 인식 |
| OpenCV | 픽셀 좌표 계산, 윤곽선 추출 | 딥러닝 마스크 → 폴리곤 변환 |
| Tesseract OCR | 숫자/텍스트 읽기 | 치수 숫자 → 스케일(mm/px) 계산 |
| Claude Vision API | 의미 이해 | 방 이름, 세대 분류, 공용부 식별 |

### 파이프라인 (이 순서 절대 변경 금지)

```
도면 이미지
    ↓
[Step 1] 딥러닝 모델 → 벽 픽셀 마스크 생성
    ↓
[Step 2] OpenCV → 마스크에서 외곽 폴리곤 추출 → pts_px
    ↓
[Step 3] Tesseract OCR → 치수 숫자 읽기 → scale_mm_per_px 계산
    ↓
[Step 4] pts_px × scale = pts_mm (정확한 mm 좌표)
    ↓
[Step 5] Claude Vision API → 방 이름, 세대(A/B/C), 공용부 식별
    ↓
ExtractionResult 반환
```

### 왜 이 구조인가
- OpenCV만으로는 외벽/치수선/테두리 구분 불가 (검증된 사실)
- Vision API만으로는 픽셀 좌표 정확도 부족 (검증된 사실)
- 딥러닝 모델이 "여기가 벽"을 픽셀 단위로 정확히 출력 → OpenCV가 좌표 계산
- Vision API는 의미 이해만 담당 (텍스트, 세대 분류)

### 절대 하지 말 것
- Vision API한테 좌표 추정, 선 추적, 치수선 계산 시키기
- OpenCV만으로 외벽/치수선 구분 시도
- 특정 도면에만 맞는 하드코딩 (top_dims, left_dims 등)
- 프롬프트 땜질로 좌표 오류 수정 시도

## 파일 구조
```
backend/
  server.py          — FastAPI 메인 서버
  extractor.py       — 도면 분석 엔진 (딥러닝+OpenCV+OCR+Vision API)
  dxf_builder.py     — pts_mm → DXF 변환
  blender_builder.py — pts_mm + rooms → Blender 스크립트
  outline.py         — 외곽 좌표 관리 (절대 변경 금지)
  validator.py       — 설계 규칙 14개 검증
  config.py          — 수치 기준값
  .env               — ANTHROPIC_API_KEY (git 제외)
docs/
  extractor_design.md — extractor 설계 문서
```

## 핵심 규칙
- 외곽 좌표(pts_mm)는 절대 변경 금지
- 노트북에서는 Obsidian MD 파일 수정 안 함
- 작업 완료 후 항상 git push
- extractor.py 수정 시 docs/extractor_design.md도 업데이트

## 개발 환경
| 환경 | 도구 |
|------|------|
| PC (집) | Claude Code + Obsidian + Blender MCP |
| 노트북 (직장) | Claude Code만 |
| 공통 | GitHub 동기화 |

## Git 워크플로우
```bash
git pull origin main   # 작업 시작 전
git add .
git commit -m "내용"
git push origin main   # 작업 완료 후
```

## API 엔드포인트
| 메서드 | 경로 | 상태 | 역할 |
|--------|------|------|------|
| POST | /api/upload | ✅ | 이미지 → 도면 분석 |
| POST | /api/convert-dxf | ✅ | pts_mm → DXF |
| POST | /api/upload-and-convert | ✅ | 이미지 → DXF 원스텝 |
| POST | /api/build3d/asis | ✅ | 외곽 → Blender 스크립트 |
| POST | /api/build3d/redesign | ✅ | 방 배치 → Blender 스크립트 |
| POST | /api/interior | 🔲 | 인테리어 스타일 (미구현) |

## 다음 작업
- [ ] extractor.py Step 1: CubiCasa5K 딥러닝 모델 통합
- [ ] extractor.py Step 2~4: 마스크→폴리곤→스케일 계산
- [ ] extractor.py Step 5: Vision API 의미 이해만 담당
- [ ] 전체 파이프라인 테스트
- [ ] Railway 배포 확인
