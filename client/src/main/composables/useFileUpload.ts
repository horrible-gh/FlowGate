import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { postFormRequest } from '@shared/api'
import { useToast } from '../components/common/useToast'

interface UploadResult {
  uploaded: Array<{ path: string; size: number }>
  skipped: Array<{ path: string; reason: string }>
}

export function useFileUpload() {
  const { t } = useI18n()
  const { showToast } = useToast()
  const uploading = ref(false)

  // processEntry / readAll — standard pattern validated against FileForgeUI FileListOverview.vue
  async function processEntry(
    entry: FileSystemEntry,
    path: string,
    allFiles: Array<File & { _relativePath?: string }>,
  ): Promise<void> {
    if (entry.isFile) {
      await new Promise<void>((resolve) => {
        ;(entry as FileSystemFileEntry).file((file) => {
          ;(file as any)._relativePath = path + file.name
          allFiles.push(file as any)
          resolve()
        })
      })
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      // readEntries returns at most 100 entries at a time — must call repeatedly until empty array is returned
      const readAll = async (): Promise<void> => {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
          reader.readEntries(resolve, reject),
        )
        if (batch.length === 0) return
        for (const sub of batch) {
          await processEntry(sub, path + entry.name + '/', allFiles)
        }
        await readAll()
      }
      await readAll()
    }
  }

  async function collectDropFiles(
    items: DataTransferItemList,
  ): Promise<Array<File & { _relativePath?: string }>> {
    const allFiles: Array<File & { _relativePath?: string }> = []
    const entries: FileSystemEntry[] = []
    for (let i = 0; i < items.length; i++) {
      const entry = items[i].webkitGetAsEntry?.()
      if (entry) entries.push(entry)
    }
    for (const entry of entries) {
      await processEntry(entry, '', allFiles)
    }
    return allFiles
  }

  async function uploadFiles(
    projectId: string,
    targetPath: string,
    files: Array<File & { _relativePath?: string }>,
    onSuccess: () => void,
  ): Promise<void> {
    if (files.length === 0) return
    uploading.value = true
    try {
      const formData = new FormData()
      formData.append('target_path', targetPath)
      // Embed the relative path in the third argument (filename) so the backend can restore the directory structure (P007 §2-3)
      for (const file of files) {
        formData.append('files[]', file, file._relativePath ?? file.name)
      }
      const res = await postFormRequest<UploadResult>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/files/upload`,
        formData,
      )
      const result: UploadResult = (res.data as any)?.data ?? res.data
      if (result.skipped && result.skipped.length > 0) {
        showToast(
          t('main.file_tree_node.toast_upload_partial', { count: result.skipped.length }),
          'warning',
        )
      } else {
        showToast(t('main.file_tree_node.toast_upload_success'), 'success')
      }
      onSuccess()
    } catch (e: any) {
      const status = e?.response?.status
      if (status === 413) {
        showToast(t('main.file_tree_node.toast_upload_too_large'), 'danger')
      } else if (status === 403) {
        showToast(t('main.file_tree_node.toast_upload_forbidden'), 'danger')
      } else {
        showToast(t('main.file_tree_node.toast_upload_failed'), 'danger')
      }
    } finally {
      uploading.value = false
    }
  }

  return { uploading, collectDropFiles, uploadFiles }
}
