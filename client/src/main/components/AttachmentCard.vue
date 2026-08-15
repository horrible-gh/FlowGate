<template>
  <!-- flowgate.default.0060 — 승인 시안 `wdkcvrmk` 네 화면(main / empty / deleting / collapsed)을
       실제 컴포넌트로 옮긴 것. 클래스 이름·구조·전환 값을 시안 그대로 쓴다.
       시안에 있는 요소는 빼지 않고, 시안에 없는 요소는 만들지 않는다(D0010 6-2, DS0009 5절):
         · `main` 화면의 [빈 상태 보기]는 시안 안에 "데모"로 표시된 시연 장치라 옮기지 않는다.
         · 별도 [다운로드] 버튼을 만들지 않는다 — 목록 줄의 파일명 자체가 내려받기 진입점이다.
         · [복사] 버튼을 만들지 않는다 — copy는 소스를 다루는 쪽이 부르는 API 통로다(D0010 6-7). -->
  <section
    class="card attach-card"
    :class="{ collapsed, 'has-files': attachments.length > 0 }"
    @dragenter="onCardDragEnter"
    @dragover="onCardDragEnter"
  >
    <div class="card-hd">
      <!-- 제목 줄 전체가 접기/펼치기 버튼이다(D0010 6-2). 캐럿 회전 -90°, 전환 0.18s,
           aria-expanded, 동작 축소 설정 처리까지 DocHeader의 접기 규칙과 같은 값을 쓴다
           (D0010 6-3 / TR0006 실측치). 새 접기 방식을 만들지 않았다. -->
      <button
        class="card-hd-toggle"
        type="button"
        :aria-expanded="!collapsed"
        :aria-controls="bodyId"
        :title="collapsed ? t('main.attachment_card.expand') : t('main.attachment_card.collapse')"
        @click="collapsed = !collapsed"
      >
        <span class="card-title">
          <AppIcon name="paperclip" style="color:var(--text-m);" />
          {{ t('main.attachment_card.title') }}
        </span>
        <AppIcon name="caret-down" class="card-hd-caret" />
        <!-- 접었을 때만 보이는 요약. 접어 둔 채로도 몇 개가 붙어 있는지 읽힌다. -->
        <span class="attach-fold-summary">{{ foldSummary }}</span>
      </button>
      <div class="card-actions">
        <span class="attach-count-pill">{{ t('main.attachment_card.count', { count: attachments.length }) }}</span>
      </div>
    </div>

    <div :id="bodyId" class="card-bd attach-card-bd">
      <!-- 드롭존: 0개일 때 큰 영역, 1개 이상일 때 얇은 한 줄 바 (D0010 6-4).
           읽기 전용(AI 실행 중)에는 올리기·지우기를 내리고 목록·내려받기만 남긴다(D0010 6-1). -->
      <div
        v-if="!readOnly"
        class="attach-dropzone"
        :class="{ 'drag-over': dragging }"
        @dragenter.prevent="dragging = true"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @dragend.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input ref="fileInputRef" type="file" multiple hidden @change="onPick" />
        <div class="attach-dz-empty">
          <AppIcon name="upload-simple" class="attach-dz-icon" />
          <p class="attach-dz-text">{{ t('main.attachment_card.drop_zone') }}</p>
          <button class="btn btn-outline btn-sm attach-select-btn" type="button" @click.stop="openPicker">
            <AppIcon name="folder-open" /> {{ t('main.attachment_card.select_files') }}
          </button>
          <p class="attach-dz-hint">{{ t('main.attachment_card.hint', { size: maxUploadLabel }) }}</p>
        </div>
        <div class="attach-dz-compact">
          <AppIcon name="plus-circle" class="attach-dz-plus" />
          <span>
            {{ t('main.attachment_card.drop_zone_compact') }}
            <button class="attach-inline-btn" type="button" @click.stop="openPicker">
              {{ t('main.attachment_card.select_inline') }}
            </button>
          </span>
        </div>
      </div>

      <ul class="attach-list">
        <!-- 목록 줄: 종류 아이콘 · 파일명 · 크기 · 삭제 버튼. 시안과 같은 네 칸이다. -->
        <li
          v-for="item in attachments"
          :key="item.filename"
          class="attach-item"
          :class="{ removing: removing.has(item.filename) }"
          @transitionend="onRemoveTransitionEnd(item.filename)"
        >
          <span class="attach-item-ico" :class="kindOf(item.filename)">
            <AppIcon :name="iconOf(item.filename)" />
          </span>
          <!-- 파일명 자체가 내려받기 진입점이다(D0010 6-2). -->
          <button
            class="attach-item-name"
            type="button"
            :title="item.filename"
            @click="download(item)"
          >{{ item.filename }}</button>
          <span class="attach-item-size">{{ formatSize(item.size) }}</span>
          <button
            v-if="!readOnly"
            class="attach-item-del"
            type="button"
            :title="t('main.attachment_card.delete')"
            @click="remove(item)"
          >
            <AppIcon name="trash" />
          </button>
        </li>
      </ul>
      <p v-if="attachments.length === 0" class="attach-empty-note">
        {{ t('main.attachment_card.empty_note') }}
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { deleteRequest, downloadBlobRequest, getRequest, postFormRequest } from '@shared/api'
import { useToast } from './common/useToast'

/** P0011 1-3 첨부 객체. 경로는 언제나 storage-상대이고 절대경로가 아니다. */
export interface AttachmentItem {
  doc_id: string
  original_filename: string
  filename: string
  size: number
  content_type: string
  content_sha256: string
  path: string
  path_base: string
  uploaded_by: string | null
  uploaded_at: string
}

const props = defineProps<{
  docId: string
  readOnly?: boolean
}>()

const emit = defineEmits<{ changed: [count: number] }>()

const { t } = useI18n()
const { showToast } = useToast()

// L0012 1-1 attach_max_upload_bytes. 안내문의 숫자와 서버 상한은 같은 값이어야 한다 —
// 지금까지 화면은 10MB, 시안 안내문은 20MB를 말하고 있었고 L0012가 20 MiB로 확정했다.
const ATTACH_MAX_UPLOAD_BYTES = 20971520

const attachments = ref<AttachmentItem[]>([])
const collapsed = ref(false)      // 기본값은 펼침 (D0010 6-3)
const dragging = ref(false)
const removing = ref<Set<string>>(new Set())
// 서버가 이미 지웠고 전환이 끝나기만 기다리는 줄. `removing`과 나눠 두는 이유: 거절당해
// 되돌아가는 줄(전환만 되감고 목록에는 남는다)과 구분해야 한다.
const pendingDrop = ref<Set<string>>(new Set())
const fileInputRef = ref<HTMLInputElement | null>(null)
const bodyId = computed(() => `attach-body-${props.docId.replace(/[^A-Za-z0-9_-]/g, '-')}`)
const maxUploadLabel = computed(() => `${Math.round(ATTACH_MAX_UPLOAD_BYTES / (1024 * 1024))}MB`)

const foldSummary = computed(() => {
  const list = attachments.value
  if (list.length === 0) return t('main.attachment_card.fold_summary_empty')
  // "외 N개"에서 N = count - 1 이 0이면 붙이지 않는다 (L0012 5절 경계 조건).
  if (list.length === 1) return t('main.attachment_card.fold_summary_one', { name: list[0].filename })
  return t('main.attachment_card.fold_summary_many', {
    name: list[0].filename,
    rest: list.length - 1,
  })
})

function apiBase(): string {
  return `/api/v1/documents/${encodeURIComponent(props.docId)}/attachments`
}

function kindOf(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase()
  if (['xls', 'xlsx'].includes(ext)) return 'xls'
  if (ext === 'csv') return 'csv'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return 'img'
  if (ext === 'pdf') return 'pdf'
  return 'file'
}

function iconOf(name: string): string {
  const kind = kindOf(name)
  if (kind === 'xls' || kind === 'csv') return 'file-xls'
  if (kind === 'img') return 'file-image'
  if (kind === 'pdf') return 'file-pdf'
  return 'file'
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

function errorCode(e: any): string {
  return e?.response?.data?.error?.code ?? ''
}

function errorMessage(e: any, fallbackKey: string): string {
  const code = errorCode(e)
  // 0060 TR0017 rev2. 반려 사유가 콘솔의 `500 (Internal Server Error)` 한 줄과 일반 실패
  // 문구뿐이었다. 서버가 저장/등록에 실패한 경우(5xx)는 사용자가 다시 시도할 수 있는 상황과
  // 다르므로, 원인이 서버에 기록됐다는 것까지 화면에서 구분해 알린다.
  const status = Number(e?.response?.status ?? 0)
  const serverSide =
    status >= 500 ||
    code === 'ATTACHMENT_STORE_FAILED' ||
    code === 'ATTACHMENT_METADATA_FAILED' ||
    code === 'ATTACHMENT_OPERATION_FAILED'
  if (serverSide) return t('main.attachment_card.error_server')
  if (code === 'DOCUMENT_NOT_MUTABLE') return t('main.attachment_card.error_not_mutable')
  if (code === 'ATTACHMENT_TOO_LARGE') {
    return t('main.attachment_card.error_too_large', { size: maxUploadLabel.value })
  }
  if (code === 'ATTACHMENT_NOT_FOUND') return t('main.attachment_card.error_not_found')
  return t(fallbackKey)
}

/** 목록 조회 (P0011 3절). 첨부 0개는 오류가 아니라 200 + 빈 배열이다. */
async function fetchList() {
  try {
    const res = await getRequest<any>(apiBase())
    const data = (res.data as any)?.data ?? res.data
    attachments.value = (data?.attachments ?? []) as AttachmentItem[]
    emit('changed', attachments.value.length)
  } catch {
    attachments.value = []
  }
}

function openPicker() {
  fileInputRef.value?.click()
}

function onCardDragEnter() {
  // 접힌 상태에서 파일을 카드 위로 끌고 오면 자동으로 펼친다 (D0010 6-3).
  if (collapsed.value) collapsed.value = false
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) void upload(files)
}

function onDrop(event: DragEvent) {
  dragging.value = false
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (files.length) void upload(files)
}

/** 업로드 (P0011 2절). 같은 이름 `file` part를 반복해 다건을 한 요청으로 보낸다. */
async function upload(files: File[]) {
  const form = new FormData()
  files.forEach((f) => form.append('file', f))
  try {
    const res = await postFormRequest<any>(apiBase(), form)
    const data = (res.data as any)?.data ?? res.data
    const added = (data?.attachments ?? []) as AttachmentItem[]
    attachments.value = [...attachments.value, ...added]
    emit('changed', attachments.value.length)
    showToast(t('main.attachment_card.uploaded', { count: added.length }), 'success')
  } catch (e: any) {
    // 실패하면 성공 전의 목록을 그대로 둔다 (P0011 2절). 한 part가 실패하면 요청 전체가
    // 실패하므로 부분 반영이 있을 수 없다.
    showToast(errorMessage(e, 'main.attachment_card.upload_failed'), 'error')
    await fetchList()
  }
}

/** 내려받기 (P0011 4절). 인증 헤더가 필요하므로 blob으로 받아 저장한다. */
async function download(item: AttachmentItem) {
  try {
    const res = await downloadBlobRequest(`${apiBase()}/${encodeURIComponent(item.filename)}`)
    const url = URL.createObjectURL(res.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = item.filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    showToast(errorMessage(e, 'main.attachment_card.download_failed'), 'error')
    await fetchList()
  }
}

/**
 * 삭제 (P0011 5절, 시안 ③ 화면).
 * 누른 줄에 `.removing`을 붙여 옅어지며 옆으로 밀리게 하고, 전환이 끝난 뒤에 목록을 다시
 * 그린다. 서버가 거절하면(예: AI 실행 중 409) 전환을 되돌려 원래 항목을 다시 표시한다.
 */
async function remove(item: AttachmentItem) {
  if (removing.value.has(item.filename)) return
  removing.value = new Set([...removing.value, item.filename])
  try {
    await deleteRequest(`${apiBase()}/${encodeURIComponent(item.filename)}`)
    pendingDrop.value = new Set([...pendingDrop.value, item.filename])
    // 전환이 실행되지 않는 환경(동작 축소 설정, 테스트 러너)에서도 목록이 남지 않도록
    // 전환 시간 뒤에 한 번 더 정리한다. transitionend가 먼저 오면 그쪽이 이긴다.
    window.setTimeout(() => onRemoveTransitionEnd(item.filename), 200)
  } catch (e: any) {
    const next = new Set(removing.value)
    next.delete(item.filename)
    removing.value = next
    showToast(errorMessage(e, 'main.attachment_card.delete_failed'), 'error')
    if (errorCode(e) === 'ATTACHMENT_NOT_FOUND') await fetchList()
  }
}

function onRemoveTransitionEnd(filename: string) {
  if (!pendingDrop.value.has(filename)) return
  const drop = new Set(pendingDrop.value)
  drop.delete(filename)
  pendingDrop.value = drop
  const left = new Set(removing.value)
  left.delete(filename)
  removing.value = left
  attachments.value = attachments.value.filter((a) => a.filename !== filename)
  emit('changed', attachments.value.length)
}

watch(() => props.docId, () => { void fetchList() }, { immediate: true })

defineExpose({ fetchList, attachments, collapsed })
</script>

<style scoped>
/* 시안 wdkcvrmk의 규칙을 그대로 옮긴다. 값이 다르면 시안과 다른 물건이 된다. */
.attach-card { margin-bottom: 14px; }

/* 아코디언 — 카드 제목 전체가 접기/펼치기 버튼.
   캐럿·회전 각도·전환 시간(0.18s)·동작 축소 처리는 DocHeader의
   `.doc-hdr-collapse-btn` / `.doc-hdr-caret` 과 같은 규칙이다(D0010 6-3). */
.card-hd-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  margin: -14px 0 -14px -18px;
  padding: 14px 10px 14px 18px;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.card-hd-toggle:hover .card-title { color: var(--primary); }
.card-hd-toggle:focus-visible { outline: 2px solid var(--info); outline-offset: -2px; }
.card-hd-caret {
  margin-left: 2px;
  color: var(--text-m);
  font-size: .7rem;
  transition: transform .18s ease;
}
.attach-card.collapsed .card-hd-caret { transform: rotate(-90deg); }
.attach-card.collapsed .card-bd { display: none; }
@media (prefers-reduced-motion: reduce) {
  .card-hd-caret { transition-duration: .1s; }
  .attach-item { transition-duration: .1s; }
}

/* 접었을 때만 보이는 요약 — 접어 둔 채로도 첨부가 몇 개인지 읽힌다. */
.attach-fold-summary {
  display: none;
  overflow: hidden;
  color: var(--text-m);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: .72rem;
}
.attach-card.collapsed .attach-fold-summary { display: inline; }

/* MainPanel의 `.card-actions` 는 그 파일의 scoped 규칙이라 자식 컴포넌트 안에는 닿지 않는다.
   같은 값을 여기 둔다. */
.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.attach-count-pill {
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-s);
  background: var(--surface-h);
  font-size: .68rem;
  font-weight: 700;
}

.attach-card-bd { padding: 0; }

.attach-dropzone {
  position: relative;
  margin-bottom: 10px;
  border: 1.5px dashed var(--border-d);
  border-radius: var(--r);
  background: var(--surface-h);
  transition: border-color .15s ease, background .15s ease;
}
.attach-dropzone.drag-over {
  border-color: var(--primary);
  background: var(--primary-l);
}
.attach-dz-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 26px 16px;
  text-align: center;
}
.attach-dz-icon { color: var(--text-m); font-size: 1.6rem; }
.attach-dz-text { margin: 0; color: var(--text-s); font-size: .82rem; }
.attach-dz-hint { margin: 2px 0 0; color: var(--text-m); font-size: .68rem; }
.attach-dz-compact {
  display: none;
  align-items: center;
  gap: 7px;
  padding: 9px 12px;
  color: var(--text-s);
  font-size: .76rem;
}
.attach-dz-plus { color: var(--primary); font-size: 1rem; }
.attach-inline-btn {
  border: 0;
  color: var(--primary);
  background: transparent;
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
}
/* 0개 → 큰 드롭존, 1개 이상 → 얇은 한 줄 바 (D0010 6-4). */
.attach-card.has-files .attach-dz-empty { display: none; }
.attach-card.has-files .attach-dz-compact { display: flex; }

.attach-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.attach-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  transition: opacity .16s ease, transform .16s ease, margin .16s ease, padding .16s ease, height .16s ease;
  overflow: hidden;
}
/* 시안 ③ 삭제 진행 중 — 옅어지며 옆으로 밀린다. */
.attach-item.removing {
  opacity: 0;
  transform: translateX(6px);
  height: 0;
  padding-top: 0;
  padding-bottom: 0;
  margin: 0;
  border-width: 0;
}
.attach-item-ico { flex: 0 0 auto; color: var(--text-m); font-size: 1.05rem; }
.attach-item-ico.xls, .attach-item-ico.csv { color: #16a34a; }
.attach-item-ico.img { color: #7c3aed; }
.attach-item-ico.pdf { color: #dc2626; }
.attach-item-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  border: 0;
  padding: 0;
  color: var(--text);
  background: transparent;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: .78rem;
  cursor: pointer;
}
.attach-item-name:hover { color: var(--primary); text-decoration: underline; }
.attach-item-size { flex: 0 0 auto; color: var(--text-m); font-size: .68rem; }
.attach-item-del {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 50%;
  color: var(--text-m);
  background: transparent;
  cursor: pointer;
}
.attach-item-del:hover { color: var(--danger); background: var(--danger-l); }
.attach-empty-note {
  margin: 0;
  color: var(--text-m);
  font-size: .72rem;
  text-align: center;
  padding: 4px 0 0;
}
</style>
