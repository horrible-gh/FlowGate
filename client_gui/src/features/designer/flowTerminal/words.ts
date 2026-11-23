// Words of the Flow terminal (0078 · 0001-R §3). Korean and English are the contract; Japanese follows the other
// Designer windows so a Japanese screen never falls back to English half-way.
type ExitWord = 'closed' | 'idle_timeout' | 'shell_exit' | 'error'

const ko = {
  title: '터미널', tabMark: '터미널 열림',
  limit: '터미널은 3개까지 열 수 있습니다 — 다른 탭의 터미널을 닫아 주세요',
  exists: '이 플로우의 터미널이 다른 브라우저 창에서 열려 있습니다',
  notAdmin: '관리자만 터미널을 쓸 수 있습니다', failed: '터미널을 열지 못했습니다. 다시 시도해 주세요.',
  noCwd: '이 플로우의 작업 폴더가 서버에 없습니다 — 워크트리나 프로젝트 체크아웃을 먼저 준비해 주세요',
  connecting: '연결 중…', cwd: '실행 위치', minimize: '최소화', restore: '복원', close: '창 닫기',
  cardRunning: '카드 실행 중 — 같은 폴더를 고치면 충돌할 수 있음',
  ended: '세션이 끝났습니다', reopen: '새로 열기',
  exit: { closed: '연결이 끊겼습니다', idle_timeout: '30분 동안 쓰지 않아 끝났습니다', shell_exit: '셸이 끝났습니다', error: '오류로 끝났습니다' } as Record<ExitWord, string>,
  exitCode: (code: number) => `종료 코드 ${code}`,
  confirmTitle: '실행 중인 명령이 있습니다', confirmBody: '창을 닫으면 실행 중인 명령도 함께 끝납니다.',
  confirmClose: '닫기', cancel: '취소', dismiss: '알림 닫기',
  // 0015-T A: 모달리스 ↔ 디자이너 (filling the Designer area under the Flow tabs).
  toDesigner: '디자이너로', toModeless: '모달리스로', endShell: '닫기 (셸 끝내기)',
}
type Words = typeof ko

const en: Words = {
  title: 'Terminal', tabMark: 'Terminal open',
  limit: 'Up to 3 terminals can be open — close the terminal of another tab',
  exists: "This Flow's terminal is open in another browser window",
  notAdmin: 'Only administrators can use the terminal', failed: 'Could not open the terminal. Please try again.',
  noCwd: "This Flow has no working folder on the server — prepare its worktree or the project checkout first",
  connecting: 'Connecting…', cwd: 'Working folder', minimize: 'Minimize', restore: 'Restore', close: 'Close window',
  cardRunning: 'A card is running — editing the same folder may conflict',
  ended: 'The session has ended', reopen: 'Open again',
  exit: { closed: 'The connection was closed', idle_timeout: 'Ended after 30 minutes without use', shell_exit: 'The shell exited', error: 'Ended with an error' },
  exitCode: code => `exit code ${code}`,
  confirmTitle: 'A command is running', confirmBody: 'Closing the window also ends the running command.',
  confirmClose: 'Close', cancel: 'Cancel', dismiss: 'Dismiss',
  toDesigner: 'To Designer', toModeless: 'To modeless', endShell: 'Close (end the shell)',
}

const ja: Words = {
  title: 'ターミナル', tabMark: 'ターミナル表示中',
  limit: 'ターミナルは3つまで開けます — 他のタブのターミナルを閉じてください',
  exists: 'このフローのターミナルは別のブラウザウィンドウで開かれています',
  notAdmin: 'ターミナルは管理者のみ使用できます', failed: 'ターミナルを開けませんでした。もう一度お試しください。',
  noCwd: 'このフローの作業フォルダがサーバーにありません — 先にワークツリーかプロジェクトのチェックアウトを用意してください',
  connecting: '接続中…', cwd: '実行フォルダ', minimize: '最小化', restore: '元に戻す', close: 'ウィンドウを閉じる',
  cardRunning: 'カード実行中 — 同じフォルダを編集すると競合する可能性があります',
  ended: 'セッションが終了しました', reopen: '新しく開く',
  exit: { closed: '接続が切れました', idle_timeout: '30分間使用されなかったため終了しました', shell_exit: 'シェルが終了しました', error: 'エラーで終了しました' },
  exitCode: code => `終了コード ${code}`,
  confirmTitle: '実行中のコマンドがあります', confirmBody: 'ウィンドウを閉じると実行中のコマンドも終了します。',
  confirmClose: '閉じる', cancel: 'キャンセル', dismiss: '通知を閉じる',
  toDesigner: 'デザイナーへ', toModeless: 'モードレスへ', endShell: '閉じる (シェル終了)',
}

export const words: Record<'ko' | 'en' | 'ja', Words> = { ko, en, ja }
export function wordsFor(locale: string): Words {
  return words[locale as keyof typeof words] ?? words.en
}
export type { ExitWord, Words }
