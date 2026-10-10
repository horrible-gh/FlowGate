import { computed } from 'vue'
import { fetchDocument } from '@/features/document'

export const betaAvailability = computed(() => fetchDocument)
