// Words of 시스템 설정 › 실행 자원 › 웹 터미널 (0078 · 0015-T B). Korean and English are the contract; Japanese follows
// the other settings screens so a Japanese screen never falls back to English half-way.
const ko = {
  title: '웹 터미널',
  sub: '디자이너 툴바의 [터미널]로 서버 PC에서 셸을 엽니다. 관리자만 쓸 수 있고, 켜는 것은 아래 [사용] 스위치뿐입니다.',
  loadFailed: '웹 터미널 설정을 불러오지 못했습니다.', retry: '다시 불러오기',
  status: '상태',
  state: { not_installed: '설치 안 됨', installing: '설치 중', installed: '설치됨', error: '오류' },
  use: '사용', on: '켜짐', off: '꺼짐', needsInstall: '설치 후 사용',
  offConfirm: (n: number) => `열려 있는 터미널 ${n}개가 바로 닫힙니다.`, turnOff: '끄기', cancel: '취소',
  bundle: '셸 부품', noBundle: '이 서버(Linux/macOS)는 받을 부품이 없습니다. 처음부터 설치되어 있습니다.',
  install: '설치', reinstall: '다시 설치',
  progress: (done: string, total: string) => `${done} / ${total}`,
  steps: { download: '받기', verify: '확인(SHA-256)', extract: '풀기', test: '시험', apply: '적용' },
  installingNote: '브라우저를 닫아도 서버에서 설치가 계속됩니다.',
  cancelled: '설치를 취소했습니다.',
  offlineHint: '인터넷이 안 되는 서버는 서버 PC에서 `setup-terminal --from-dir <부품 폴더>`로 설치하세요.',
  test: '셸 시험', testing: '시험 중…', testOk: (shell: string) => `셸을 띄웠다 닫았습니다 · ${shell}`,
  remove: '삭제', removeConfirm: '받은 셸 부품을 삭제합니다. 다시 쓰려면 다시 설치해야 합니다.', removed: '셸 부품을 삭제했습니다.',
  removeNeedsOff: '켜져 있는 동안은 삭제할 수 없습니다. 먼저 [사용]을 끄세요.',
  closedSessions: (n: number) => `열려 있던 터미널 ${n}개를 닫았습니다.`,
  errors: {
    terminal_install_running: '이미 설치 중입니다.',
    terminal_bundle_not_required: '이 서버는 받을 부품이 없습니다.',
    terminal_not_installed: '설치가 끝나야 켤 수 있습니다.',
    terminal_enabled: '켜져 있는 동안은 삭제할 수 없습니다. 먼저 [사용]을 끄세요.',
    terminal_download_failed: '부품을 받지 못했습니다.',
  } as Record<string, string>,
  failed: '요청을 처리하지 못했습니다. 다시 시도해 주세요.',
}
type Words = typeof ko

const en: Words = {
  title: 'Web terminal',
  sub: "Opens a shell on the server PC from the Designer toolbar's [Terminal]. Administrators only; only the [Use] switch below turns it on.",
  loadFailed: 'Could not load the web terminal settings.', retry: 'Reload',
  status: 'Status',
  state: { not_installed: 'Not installed', installing: 'Installing', installed: 'Installed', error: 'Error' },
  use: 'Use', on: 'On', off: 'Off', needsInstall: 'Available after installing',
  offConfirm: n => `${n} open terminal${n === 1 ? '' : 's'} will be closed at once.`, turnOff: 'Turn off', cancel: 'Cancel',
  bundle: 'Shell component', noBundle: 'This server (Linux/macOS) needs nothing to download. It is installed from the start.',
  install: 'Install', reinstall: 'Install again',
  progress: (done, total) => `${done} / ${total}`,
  steps: { download: 'Download', verify: 'Verify (SHA-256)', extract: 'Extract', test: 'Test', apply: 'Apply' },
  installingNote: 'The installation goes on on the server even if this browser is closed.',
  cancelled: 'The installation was cancelled.',
  offlineHint: 'For a server without internet, install on the server PC with `setup-terminal --from-dir <component folder>`.',
  test: 'Test shell', testing: 'Testing…', testOk: shell => `Started and closed a shell · ${shell}`,
  remove: 'Delete', removeConfirm: 'Deletes the downloaded shell component. It has to be installed again to be used.', removed: 'The shell component was deleted.',
  removeNeedsOff: 'It cannot be deleted while on. Turn [Use] off first.',
  closedSessions: n => `Closed ${n} open terminal${n === 1 ? '' : 's'}.`,
  errors: {
    terminal_install_running: 'An installation is already running.',
    terminal_bundle_not_required: 'This server needs nothing to download.',
    terminal_not_installed: 'It can be turned on once installed.',
    terminal_enabled: 'It cannot be deleted while on. Turn [Use] off first.',
    terminal_download_failed: 'Could not download the component.',
  },
  failed: 'The request failed. Please try again.',
}

const ja: Words = {
  title: 'Web ターミナル',
  sub: 'デザイナーのツールバーの[ターミナル]でサーバー PC のシェルを開きます。管理者のみ使用でき、オンにするのは下の[使用]スイッチだけです。',
  loadFailed: 'Web ターミナルの設定を読み込めませんでした。', retry: '再読み込み',
  status: '状態',
  state: { not_installed: '未インストール', installing: 'インストール中', installed: 'インストール済み', error: 'エラー' },
  use: '使用', on: 'オン', off: 'オフ', needsInstall: 'インストール後に使用',
  offConfirm: n => `開いているターミナル ${n} 個がすぐに閉じます。`, turnOff: 'オフにする', cancel: 'キャンセル',
  bundle: 'シェル部品', noBundle: 'このサーバー (Linux/macOS) は取得する部品がありません。最初からインストール済みです。',
  install: 'インストール', reinstall: '再インストール',
  progress: (done, total) => `${done} / ${total}`,
  steps: { download: '取得', verify: '確認(SHA-256)', extract: '展開', test: 'テスト', apply: '適用' },
  installingNote: 'ブラウザーを閉じてもサーバーでインストールが続きます。',
  cancelled: 'インストールをキャンセルしました。',
  offlineHint: 'インターネットに接続できないサーバーは、サーバー PC で `setup-terminal --from-dir <部品フォルダ>` を実行してください。',
  test: 'シェルテスト', testing: 'テスト中…', testOk: shell => `シェルを起動して閉じました · ${shell}`,
  remove: '削除', removeConfirm: '取得したシェル部品を削除します。再び使うには再インストールが必要です。', removed: 'シェル部品を削除しました。',
  removeNeedsOff: 'オンの間は削除できません。先に[使用]をオフにしてください。',
  closedSessions: n => `開いていたターミナル ${n} 個を閉じました。`,
  errors: {
    terminal_install_running: 'すでにインストール中です。',
    terminal_bundle_not_required: 'このサーバーは取得する部品がありません。',
    terminal_not_installed: 'インストールが終わるとオンにできます。',
    terminal_enabled: 'オンの間は削除できません。先に[使用]をオフにしてください。',
    terminal_download_failed: '部品を取得できませんでした。',
  },
  failed: '要求を処理できませんでした。もう一度お試しください。',
}

export const systemTerminalWords: Record<'ko' | 'en' | 'ja', Words> = { ko, en, ja }
export function systemTerminalWordsFor(locale: string): Words {
  return systemTerminalWords[locale as keyof typeof systemTerminalWords] ?? systemTerminalWords.en
}
export type { Words as SystemTerminalWords }
