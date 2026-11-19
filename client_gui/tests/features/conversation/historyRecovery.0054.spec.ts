import { flushPromises } from '@vue/test-utils'
import { afterEach, expect, it } from 'vitest'
import { createConversationSession, type ConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, turn } from './fakeConversationSource'
const scope={projectId:'p',groupId:'p.m.0001'}
const chat='p.m.0001.0002-CH'
let session:ConversationSession|undefined
afterEach(()=>session?.dispose())
it('pages beyond 100 and does not fetch details for 140 proven replies',async()=>{
  const replies=Array.from({length:140},(_,i)=>turn(i+1,'ai','answer',null,{sourceRunId:`r${i}`}))
  const fake=createFakeSource({chats:[{id:chat,title:'Long chat',updatedAt:null}],turns:{[chat]:replies}})
  fake.source.listRuns.mockImplementation(async (_scope:unknown,_limit:number,cursor?:string)=>{
    const start=Number(cursor??0), end=Math.min(start+20,140)
    return {items:replies.slice(start,end).map(t=>({runId:t.sourceRunId!,docRef:chat,result:{runId:t.sourceRunId!,docRef:chat,groupId:scope.groupId,actionScope:'chat',status:'finished' as const,endReason:'exited',outcome:'complete',exitCode:0}})),total:null,hasMore:end<140,nextCursor:end<140?String(end):null}
  })
  session=createConversationSession({source:fake.source})
  await session.setScope(scope);await flushPromises()
  expect(session.state.turns).toHaveLength(140)
  fake.source.fetchRun.mockClear()
  await session.recover({history:true})
  expect(fake.source.listRuns.mock.calls.some(c=>c[2]==='120')).toBe(true)
  expect(fake.source.fetchRun).not.toHaveBeenCalled()
  expect(session.state.activities.some(a=>a.kind==='status'&&a.code==='run_history_unconfirmed')).toBe(false)
})
it('names a genuinely failed run, supports dismiss, and retries it',async()=>{
  const fake=createFakeSource({chats:[{id:chat,title:'Chat',updatedAt:null}],turns:{[chat]:[]}})
  fake.source.listRuns.mockResolvedValue({items:[{runId:'failed-run',docRef:chat}],total:1,hasMore:false})
  fake.source.fetchRun.mockRejectedValue(new Error('offline'))
  session=createConversationSession({source:fake.source})
  await session.setScope(scope);await flushPromises()
  expect(session.state.activities).toContainEqual(expect.objectContaining({code:'run_history_unconfirmed',text:'failed-run'}))
  session.dismissHistoryWarning()
  expect(session.state.activities.some(a=>a.kind==='status'&&a.code==='run_history_unconfirmed')).toBe(false)
  fake.source.fetchRun.mockResolvedValue({runId:'failed-run',docRef:chat,actionScope:'chat',groupId:scope.groupId,status:'finished',endReason:'exited',outcome:'none',exitCode:0})
  await session.recover({history:true})
  expect(session.state.activities.some(a=>a.kind==='status'&&a.code==='run_history_unconfirmed')).toBe(false)
  expect(fake.source.startChatRun).not.toHaveBeenCalled()
})
