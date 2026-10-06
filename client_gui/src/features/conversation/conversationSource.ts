// flowgate.v02.0031 — the one data boundary of the Conversation part. The session talks only
// to a ConversationSource; the default one is the HTTP source below, built from the
// conversation / AI-run / chat-settings contracts in api.ts. Tests (and a later container that
// wants a different backend) hand in their own.
//
// flowgate.v02.0032 connects the HTTP source to the Conversation production contract (D0006
// §5.5): createChat makes a standalone CH, activities are the chat's command requests and run
// changes with their server anchors, and command decisions go to the server. There is no push
// channel yet — the HTTP source leaves `subscribeActivities` undefined and the session
// re-reads activities on its AI-run polling ticks instead (D0006 §3.9.2).
import * as api from './api'
import type {
  ChatSettings,
  ChatSettingsPatch,
  ChatSummary,
  CommandDecision,
  CommandRequestActivity,
  ConversationActivity,
  ConversationScope,
  ConversationTurn,
  AiRunState,
  ProviderList,
  RunFileDiff,
  TurnsPage,
} from './types'

export interface ConversationSource {
  listChats(scope: ConversationScope): Promise<ChatSummary[]>
  renameChat(chatId: string, title: string): Promise<ChatSummary>
  createChat(scope: ConversationScope, title: string): Promise<ChatSummary>

  fetchTurns(chatId: string, query: api.TurnsQuery): Promise<TurnsPage>
  postTurn(chatId: string, input: api.PostTurnInput): Promise<{ turn: ConversationTurn | null; headSeq: number }>
  markRead(chatId: string, lastReadSeq: number): Promise<number | null>

  /** True when this source can list activities and take command decisions. */
  readonly supportsActivities: boolean
  /** Every command request and run change of the chat — the full server list, not a delta. */
  listActivities(chatId: string): Promise<ConversationActivity[]>
  decideCommand(chatId: string, commandId: string, decision: CommandDecision): Promise<CommandRequestActivity>
  /** Stored run start / run end content of one path of a run change. */
  fetchRunFileDiff(chatId: string, runId: string, path: string): Promise<RunFileDiff>
  /** Optional push channel for activities; returns the unsubscribe function. */
  subscribeActivities?(chatId: string, listener: (activity: ConversationActivity) => void): () => void

  listProviders(projectId: string): Promise<ProviderList>
  startChatRun(chatId: string, providerId: string | null): Promise<string | null>
  fetchRun(runId: string): Promise<AiRunState>
  cancelRun(runId: string): Promise<void>
  fetchActiveChatRun(chatId: string): Promise<string | null>

  fetchChatSettings(): Promise<ChatSettings>
  saveChatSettings(patch: ChatSettingsPatch): Promise<ChatSettings>
  issueChatMention(chatId: string): Promise<string>
}

export function createHttpConversationSource(): ConversationSource {
  return {
    listChats: api.fetchChats,
    renameChat: api.renameChat,
    createChat: api.createChat,
    fetchTurns: api.fetchTurns,
    postTurn: api.postTurn,
    markRead: api.markRead,
    supportsActivities: true,
    listActivities: api.fetchActivities,
    decideCommand: api.decideCommand,
    fetchRunFileDiff: api.fetchRunFileDiff,
    listProviders: api.fetchProviders,
    startChatRun: api.startChatRun,
    fetchRun: api.fetchRun,
    cancelRun: api.cancelRun,
    fetchActiveChatRun: api.fetchActiveChatRun,
    fetchChatSettings: api.fetchChatSettings,
    saveChatSettings: api.saveChatSettings,
    issueChatMention: api.issueChatMention,
  }
}
