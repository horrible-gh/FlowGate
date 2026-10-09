import axios from 'axios'

export const attachmentsList = (docId: string) => axios.get(`/api/v1/documents/${docId}/attachments`)
