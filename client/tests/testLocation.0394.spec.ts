import { readdirSync, statSync } from 'node:fs'
import { relative, resolve } from 'node:path'

/**
 * flowgate.default.0394 T0014 (NR0003 §8.3 / §13-14) — 클라이언트 테스트는 한 뿌리에 산다.
 *
 * 0394 T0004 가 `client/src/**\/__tests__/**` 에 흩어져 있던 스펙 7 개를 `client/tests/`
 * 아래로 모았다. 옮기는 것만으로는 되돌아온다 — 다음 사람은 컴포넌트 옆에 `__tests__`
 * 폴더를 만드는 것이 자연스럽다고 느끼고, vitest 는 두 자리 모두를 수집하므로 아무도
 * 그것을 알려주지 않는다. 나뉜 채로 두면 값이 두 가지 방식으로 사라진다.
 *
 *   1. 파일 목록으로 스펙을 지정하는 실행(TS 시나리오, CI 스크립트)이 한쪽 뿌리만 적는다.
 *      vitest 는 존재하지 않는 경로를 넘겨도 조용히 exit 0 이라, 빠진 것이 무증상이다.
 *   2. `tests/setup/blockNetwork.ts` 나 `tests/helpers/mountMainPanel.ts` 같은 공용 장치가
 *      상대경로로 잡혀 있어, 다른 뿌리에서는 각자 다시 만들게 된다 — 실제 XHR 을 열던
 *      4 개 파일이 그렇게 생겼다.
 *
 * NR0003 §5.3 의 요지 그대로다: 규칙이 전역이면 검사도 전역이어야 한다. 그래서 이
 * 스펙은 `client/src` 전체를 훑는다.
 */

const CLIENT_DIR = resolve(__dirname, '..')
const SRC_DIR = resolve(CLIENT_DIR, 'src')

const SPEC_FILE = /\.(spec|test)\.[cm]?[jt]sx?$/

function walk(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === 'dist') continue
    const full = resolve(dir, entry)
    if (statSync(full).isDirectory()) walk(full, found)
    else if (SPEC_FILE.test(entry)) found.push(relative(CLIENT_DIR, full).replace(/\\/g, '/'))
  }
  return found
}

describe('클라이언트 테스트 위치', () => {
  it('제품 소스 트리(client/src) 안에는 스펙이 하나도 없다', () => {
    const strays = walk(SRC_DIR)
    expect(
      strays,
      `client/src 안에서 스펙을 찾았습니다: ${strays.join(', ')}\n` +
        '클라이언트 테스트는 client/tests/ 아래에 둡니다 (TESTING.md §2 「클라이언트에서 더」).',
    ).toEqual([])
  })

  it('공용 장치는 tests 뿌리에 있고 스펙에서 닿는다', () => {
    // 위 규칙의 근거 절반이 이것이다 — 한 뿌리라야 이 둘을 상대경로로 함께 쓸 수 있다.
    const shared = walk(resolve(CLIENT_DIR, 'tests'))
    expect(shared.length).toBeGreaterThan(0)
    expect(() => statSync(resolve(CLIENT_DIR, 'tests/setup/blockNetwork.ts'))).not.toThrow()
    expect(() => statSync(resolve(CLIENT_DIR, 'tests/helpers/mountMainPanel.ts'))).not.toThrow()
  })
})
