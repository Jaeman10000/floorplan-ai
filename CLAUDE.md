# floorplan-ai 프로젝트 컨텍스트

## 프로젝트 개요
AI 도면 분석 3D 공간 재설계 플랫폼 (SaaS)
- 도면 이미지 업로드 → OpenCV 외곽 추출 → DXF 변환 → Blender 3D → 웹 뷰어
- GitHub: Jaeman10000/floorplan-ai
- Railway 배포: floorplan-ai-production-c244.up.railway.app

## 파일 구조
```
backend/
  server.py          — FastAPI 메인 서버 (엔드포인트 6개)
  extractor.py       — OpenCV 외곽 추출 → ExtractionResult 반환
  dxf_builder.py     — pts_mm → DXF 변환 (레이어/치수선/메타)
  blender_builder.py — pts_mm + rooms → Blender Python 스크립트 생성
  outline.py         — 외곽 좌표 관리 (절대 변경 금지)
  validator.py       — 설계 규칙 14개 검증
  config.py          — 수치 기준값
```

## 핵심 규칙
- 외곽 좌표(pts_mm)는 절대 변경 금지 — 1mm라도 바꾸면 전체 설계 무효
- 방 배치는 외곽 폴리곤 안에서만
- **노트북 환경에서는 Obsidian MD 파일 수정 안 함** (Obsidian은 PC에만 설치)
- 작업 완료 후 항상 git push

## 개발 환경
| 환경 | 도구 |
|------|------|
| PC (집) | Claude Code + Obsidian + Blender MCP |
| 노트북 (직장) | Claude Code만 |
| 공통 | GitHub으로 동기화 |

### Git 워크플로우
```bash
# 작업 시작 전 항상
git pull origin main

# 작업 완료 후 항상
git add .
git commit -m "작업내용"
git push origin main
```

## API 엔드포인트
| 메서드 | 경로 | 상태 | 역할 |
|--------|------|------|------|
| POST | /api/upload | ✅ 완성 | 이미지 → 외곽 추출 |
| POST | /api/convert-dxf | ✅ 완성 | pts_mm → DXF 다운로드 |
| POST | /api/upload-and-convert | ✅ 완성 | 이미지 → DXF 원스텝 |
| POST | /api/build3d/asis | ✅ 완성 | 외곽 → Blender 스크립트 |
| POST | /api/build3d/redesign | ✅ 완성 | 방 배치 → Blender 스크립트 |
| POST | /api/interior | 🔲 미구현 | 인테리어 스타일 적용 |

## 파이프라인 흐름
```
이미지 업로드
    ↓
extractor.py — OpenCV 외곽 추출 → pts_mm
    ↓
dxf_builder.py — pts_mm → DXF 파일
    ↓
blender_builder.py — pts_mm + rooms → Blender 스크립트
    ↓
(미구현) GLB export → 웹 뷰어
```

## 다음 작업 목록
- [ ] 프론트엔드 UI (파일 업로드 + 3D 뷰어)
- [ ] Blender 스크립트 → GLB export 연동
- [ ] Three.js 웹 뷰어
- [ ] Railway 실제 동작 테스트
- [ ] /api/interior 구현
