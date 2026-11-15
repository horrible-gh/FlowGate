import { getRequest, postRequest, putRequest } from '@shared/api'
export interface Message { role: 'user' | 'assistant'; content: string }
export interface Reply { message: string; choices: { label: string; recommended: boolean }[]; policy: string | null }
export interface Result { status: 'running' | 'completed' | 'failed'; reply?: Reply; error_code?: string }
const path = '/api/v1/me/question-policy'
export const loadPolicy = async () => (await getRequest<{ body: string; updated_at: string | null }>(path)).data
export const savePolicy = async (body: string) => (await putRequest<{ body: string }>(path, { body })).data
export const loadProviders = async () => (await getRequest<{ providers: { id: string; name: string }[] }>(path + '/assistant/providers')).data.providers
export const startAssistant = async (provider_id: string, body: string, messages: Message[], locale: string) =>
  (await postRequest<{ request_id: string }>(path + '/assistant', { provider_id, body, messages, locale })).data
export const assistantResult = async (id: string) => (await getRequest<Result>(path + '/assistant/' + encodeURIComponent(id))).data
