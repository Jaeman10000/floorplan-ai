# floorplan-ai 프로젝트 컨텍스트

## 프로젝트 개요
AI 도면 분석 3D 공간 재설계 플랫폼 (SaaS)
- 도면 이미지 업로드 → 도면 분석 → DXF 변환 → Blender 3D → 웹 뷰어
- GitHub: Jaeman10000/floorplan-ai
- Railway 배포: floorplan-ai-production-c244.up.railway.app

## 파일 구조
```
backend/
  server.py          — FastAPI 메인 서버 (엔드포인트 6개)
  extractor.py       — 도면 분석 엔진 (Vision API + OpenCV fallback)
  dxf_builder.py     — pts_mm → DXF 변환 (레이어/치수선/메타)
  blender_builder.py — pts_mm + rooms → Blender Python 스크립트 생성
  outline.py         — 외곽 좌표 관리 (절대 변경 금지)
  validator.py       — 설계 규칙 14개 검증
  config.py          — 수치 기준값
  .env               — ANTHROPIC_API_KEY (git 제외, 로컬만)
docs/
  extractor_design.md — extractor.py 설계 문서 (상세)
  floorplan_ai_report.docx — 개발 보고서
```

## extractor.py 핵심 설계 원칙 ⚠️
**Vision API는 숫자 읽기만. 코드가 계산+검증+좌표 생성.**

- Vision API 역할: 도면 4면 치수선 숫자 나열, 세대명/면적/방이름 읽기
- 코드 역할: 사선 dx/dy 계산, 오각형 5개 좌표 생성, 검증
- 절대 금지: Vision API에게 좌표 추정, 선 추적, 픽셀 계산 시키는 것
- 세대: 반드시 3개(A/B/C), 공용부(18.41m²)는 common_areas로만

```
diagonal_dx = sum(top_dims) - sum(bottom_dims)
diagonal_dy = sum(left_dims) - sum(right_dims)
P0=(0,0) P1=(sum_top,0) P2=(sum_top,sum_right)
P3=(sum_top+dx, sum_right+dy) P4=(0,sum_left)
```

자세한 설계는 docs/extractor_design.md 참고

## 핵심 규칙
- 외곽 좌표(pts_mm)는 절대 변경 금지
- 방 배치는 외곽 폴리곤 안에서만
- **노트북 환경에서는 Obsidian MD 파일 수정 안 함**
- 작업 완료 후 항상 git push
- extractor.py 수정 시 반드시 docs/extractor_design.md도 함께 업데이트

## 개발 환경
| 환경 | 도구 |
|------|------|
| PC (집) | Claude Code + Obsidian + Blender MCP |
| 노트북 (직장) | Claude Code만 |
| 공통 | GitHub으로 동기화 |

### Git 워크플로우
```bash
git pull origin main   # 작업 시작 전
git add .
git commit -m "내용"
git push origin main   # 작업 완료 후
```

## API 엔드포인트
| 메서드 | 경로 | 상태 | 역할 |
|--------|------|------|------|
| POST | /api/upload | ✅ | 이미지 → 도면 분석 (Vision API) |
| POST | /api/convert-dxf | ✅ | pts_mm → DXF 다운로드 |
| POST | /api/upload-and-convert | ✅ | 이미지 → DXF 원스텝 |
| POST | /api/build3d/asis | ✅ | 외곽 → Blender 스크립트 |
| POST | /api/build3d/redesign | ✅ | 방 배치 → Blender 스크립트 |
| POST | /api/interior | 🔲 | 인테리어 스타일 적용 (미구현) |

## 파이프라인 흐름
```
이미지 업로드
    ↓
extractor.py — Vision API 치수 읽기 → 코드가 좌표 계산 → pts_mm (5개)
    ↓
dxf_builder.py — pts_mm → DXF 파일
    ↓
blender_builder.py — pts_mm + rooms → Blender 스크립트
    ↓
(미구현) GLB export → Three.js 웹 뷰어
```

## 다음 작업 목록
- [ ] extractor.py 새 구조 테스트 및 검증
- [ ] Blender 스크립트 → GLB export 연동
- [ ] Three.js 웹 뷰어 세대 구획 표시
- [ ] /api/interior 구현
- [ ] Railway 전체 파이프라인 엔드-투-엔드 테스트
