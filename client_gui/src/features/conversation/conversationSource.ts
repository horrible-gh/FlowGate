// the one data boundary of the Conversation part. The session talks only
// to a ConversationSource; the default one is the HTTP source below, built from the
// conversation / AI-run / chat-settings contracts in api.ts. Tests (and a later container that
// wants a different backend) hand in their own.
//
// connects the HTTP source to the Conversation production contract (the design
// §5.5): createChat makes a standalone CH, activities are the chat's command requests and run
// changes with their server anchors, and command decisions go to the server. There is no push
// channel yet — the HTTP source leaves `subscribeActivities` undefined and the session
// re-reads activities on its AI-run polling ticks instead.
// server events still do not carry state through this source; the panel
// hears them from the container's project stream (conversationEvents.ts) as signals and the
// session reads through this source again (session.recover).
import * as api from './api'
import type { ChatErrorRecord } from './chatErrors'
import type {
  ChatDocSettings,
  ChatDocSettingsPatch,
  ChatSettings,
  ChatSettingsPatch,
  ChatSummary,
  CommandDecision,
  CommandRequestActivity,
  ConversationActivity,
  ConversationScope,
  ConversationTurn,
  AiRunState,
  AiRunList,
  ProviderOrigin,
  ProviderList,
  RunFileDiff,
  TurnsPage,
} from './types'

export interface ConversationSource {
  listChats(scope: ConversationScope): Promise<ChatSummary[]>
  renameChat(chatId: string, title: string): Promise<ChatSummary>
  createChat(scope: ConversationScope, title: string, options?: api.CreateChatOptions): Promise<ChatSummary>
  /** The one AI title of a chat created with `autoTitle`; optional. */
  autoTitleChat?(chatId: string, providerId: string): Promise<ChatSummary>

  mutateTurn?(chatId: string, seq: number, version: number, body: string | null): Promise<ConversationTurn>
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
  /** `clientRequestId` names the press; a resend with the same id gets the run already admitted. */
  /** `providerOrigin` — how the AI was chosen; kept on the run (client_provider_origin). */
  startChatRun(chatId: string, providerId: string | null, clientRequestId?: string, providerOrigin?: ProviderOrigin): Promise<string | null>
  fetchRun(runId: string): Promise<AiRunState>
  listRuns(scope: ConversationScope, limit: number, cursor?: string, chatId?: string): Promise<AiRunList>
  cancelRun(runId: string): Promise<void>
  /**
   * B: stops a press by its `clientRequestId` before /start named its run. Optional: without it a
   * starting call cannot be stopped (the behaviour before).
   */
  cancelChatStart?(chatId: string, clientRequestId: string): Promise<api.ChatStartCancel>
  fetchActiveChatRun(chatId: string): Promise<string | null>
  /**
   * flowgate.v02test.0067 #2: the status of the Flow's (group's) current Graph run — `running` / `paused` / `stopping` /
   * `idle` …, or null without a run. Read while an edit / edit-once / execute call waits for the Flow. Optional: a
   * source without it lets the waiting call try again on every wait tick.
   */
  fetchFlowRunStatus?(flowId: string): Promise<string | null>

  fetchChatSettings(scope?: ConversationScope): Promise<ChatSettings>
  saveChatSettings(patch: ChatSettingsPatch, scope?: ConversationScope): Promise<ChatSettings>
  issueChatMention(chatId: string): Promise<string>
  /**
   * the user's AI pick and context checkpoint of one chat. Optional: a source without them keeps
   * one selection for every chat and no checkpoint (the behaviour before).
   */
  fetchChatDocSettings?(chatId: string): Promise<ChatDocSettings>
  saveChatDocSettings?(chatId: string, patch: ChatDocSettingsPatch): Promise<ChatDocSettings>
  /** B: stores a refused request of the chat in its history; optional (a source without it keeps errors locally). */
  recordChatError?(chatId: string, record: ChatErrorRecord): Promise<void>
}

export function createHttpConversationSource(): ConversationSource {
  return {
    listChats: api.fetchChats,
    renameChat: api.renameChat,
    createChat: api.createChat,
    autoTitleChat: api.autoTitleChat,
    mutateTurn: api.mutateTurn,
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
    listRuns: api.fetchChatRuns,
    cancelRun: api.cancelRun,
    cancelChatStart: api.cancelChatStart,
    fetchActiveChatRun: api.fetchActiveChatRun,
    fetchFlowRunStatus: api.fetchFlowRunStatus,
    fetchChatSettings: api.fetchChatSettings,
    saveChatSettings: api.saveChatSettings,
    issueChatMention: api.issueChatMention,
    fetchChatDocSettings: api.fetchChatDocSettings,
    saveChatDocSettings: api.saveChatDocSettings,
    recordChatError: api.recordChatError,
  }
}
