# tests/visual/gitSettingsMockup — Git 반영 박스 브라우저 실측

flowgate.v02test.0002 R0001 요구사항 2, 0010-T §5. 실제 `CheckSettings.vue` Git 박스와 승인 시안
`flowgate_git_settings_mockup.html`(R0001 첨부, sha256 `effa4c1d…caeb3`)을 같은 화면에서 위아래로 렌더링합니다.
그런 다음 치수·겹침·배치를 잽니다. vitest 스펙이 아니므로 `npm run test`에는 들어가지 않습니다.
운영 번들에도 들어가지 않습니다.

## 실행

```
cd client_gui
node tests/visual/gitSettingsMockup/measure.mjs <저장소 밖 출력 폴더> <flowgate_git_settings_mockup.html>
```

- 이 패키지의 vite·@vitejs/plugin-vue로 `harness.ts`를 production 모드로 묶습니다(`vite build`와 같은 템플릿 컴파일).
  그 결과를 headless Chromium(Edge, 없으면 Chrome)으로 엽니다.
- 서버에 접속하지 않습니다. 값은 컴포넌트의 `view` prop fixture입니다. 세 상태를 씁니다: 조회 시간 초과(보관 목록 없음: 새로고침·오류 표시, 선택 비활성), 조회 성공, 조회 시간 초과 + 보관 목록(T-b, 0006-T: 선택 활성 + 새로고침·오류 표시).
- 출력: `results.json`(측정값)과 `capture_<case>.png`(위: 시안, 아래: 현재 구현 두 상태).
- 실제 박스에 겹침이 있거나 박스 내용 밖으로 나간 컨트롤이 있으면 종료 코드 1로 끝납니다.

## 폭 구간

| case | Git 박스 바깥 폭 | 시안 viewport | 대응 Inspector 폭 |
| --- | --- | --- | --- |
| wide1026 | 1026 | 1080 | 시안 기준 폭 |
| normal910 | 910 | 964 | 938px (1920px 창, 기본 Dock) |
| mid570 | 570 | 600 | 598px (약 1440px 창) |
| narrow392 | 392 | 422 | 420px |
| min236 | 236 | 266 | 264px (최소) |

Git 박스 바깥 폭은 Inspector 폭에서 `.designer-inspector-body`의 좌우 padding 14px씩을 뺀 값입니다.

## 이 폴더의 결과

`results.json`과 `capture_*.png`는 TR0012 rework에서 Self-check `scr_bca5012ec1d64ad7ad4a71ad`로 만든 산출물입니다.
그 실행의 attempt는 `att_1f97bf3d53458a45e55969c7`, 시작 커밋은 `34c096025e234b486c5def4cd8176151bf9a4874`(미커밋 변경 포함)입니다.
측정 대상 파일의 sha256은 `results.json`의 `hashes`에 있습니다(당시 하네스는 두 상태).
T-b(TR0013 rev 1)는 T-a와 합친 상태에서 세 상태로 다시 측정했습니다(Self-check `scr_067776f9c5a44bc78430a265`, 최종 트리 재측정 `scr_45e9f35dd87641639b1aa0c1`). 산출물은 저장소 밖에 두었고, 측정값과 해시는 TR0013 §4.4에 있습니다.
