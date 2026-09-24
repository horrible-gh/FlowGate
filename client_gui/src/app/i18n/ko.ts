// v0.2 (/app) screen strings, merged into the shared i18n instance under `gui.*`
// (see ./index.ts). Copy follows the approved 01-dashboard.html; lines broken with <br/>
// in the mockup are "\n" here and rendered with white-space: pre-line.
export default {
  brand: {
    tagline: 'Turn Ideas into Working Flow',
    home_aria: 'FlowGate 대시보드로 이동',
  },
  topbar: {
    search_placeholder: '프로젝트, 그룹, 파일, 설정 등을 검색하세요...',
    search_aria: '검색 (준비 중)',
    search_unavailable: '검색은 아직 준비 중입니다.',
    notifications_aria: '알림 (준비 중)',
    switch_aria: 'GUI 전환',
    user_role_admin: '관리자',
    user_role_member: '사용자',
  },
  nav: {
    aria: '주요 화면',
    dashboard: { title: '대시보드', sub: '홈으로 돌아가기' },
    projects: { title: '프로젝트', sub: '프로젝트 관리' },
    designer: { title: '디자이너', sub: '그룹 · 흐름 설계' },
    git: { title: 'Git', sub: '저장소 관리' },
    ai: { title: 'AI 설정', sub: 'AI 그룹 및 정책' },
    settings: { title: '환경 설정', sub: '워크스페이스 설정' },
  },
  sidebar: {
    help: '도움말',
    help_unavailable: '도움말은 아직 준비 중입니다.',
    version: 'FlowGate v0.2',
    build: 'Build {build}',
  },
  dashboard: {
    greeting_line1: '좋은 하루입니다.',
    greeting_line2: '지금, 새로운 가능성을 이어가세요.',
    lead: 'FlowGate는 아이디어를 실제 작업으로 이어가는 공간입니다.',
    quote: '“작은 아이디어도\n올바른 흐름을 만나면\n큰 변화를 만듭니다.”',
    quote_by: '— FlowGate',
    cards_aria: '화면 바로가기',
    unavailable: '준비 중',
    cards: {
      projects: {
        title: '프로젝트',
        desc: '최근 프로젝트를 열거나\n기존 프로젝트를 불러와서\n작업을 이어가세요.',
        action: '프로젝트 열기',
      },
      designer: {
        title: '디자이너',
        desc: '그룹과 작업의 흐름을 설계하고\n시각 함께 워크플로우를 만들어보세요.',
        action: '디자이너로 이동',
      },
      git: {
        title: 'Git',
        desc: '저장소를 연결하고\n브랜치와 변경사항을 동기화하세요.',
        action: 'Git 관리하기',
      },
      ai: {
        title: 'AI 설정',
        desc: 'AI 그룹, 역할, 제공자, 정책을\n설정하고 관리하세요.',
        action: 'AI 설정 열기',
      },
      settings: {
        title: '환경 설정',
        desc: '워크스페이스와 사용자 설정을\n관리하세요.',
        action: '설정 열기',
      },
      quick: {
        title: '빠른 시작',
        desc: '자주 사용하는 작업으로\n빠르게 시작하세요.',
        action: '바로 시작하기',
      },
    },
    tip_label: '빠른 팁',
    tip_text: '검색(Ctrl + K)은 아직 준비 중입니다. 지금은 왼쪽 메뉴나 위 카드로 각 화면에 이동할 수 있습니다.',
    tip_close: '팁 닫기',
    side_aria: '작업 요약',
    panels: {
      recent_projects: '최근 프로젝트',
      recent_groups: '최근 그룹',
      attention: '주의 필요',
      notices: '시스템 알림',
    },
    panel_empty: '아직 연결되지 않았습니다. 후속 작업에서 실제 데이터로 채워집니다.',
    footer_mark: 'Build the Flow, Not Just the Document.',
  },
  placeholder: {
    badge: '준비 중',
    body: '이 화면은 FlowGate v0.2에서 아직 구현되지 않았습니다.',
    legacy_hint: '지금 이 기능이 필요하면 상단의 [{label}] 버튼으로 v0.1 화면을 사용하세요.',
    back: '대시보드로 돌아가기',
  },
  not_found: {
    title: '화면을 찾을 수 없습니다',
    body: '{path} 에 해당하는 v0.2 화면이 없습니다.',
  },
}
