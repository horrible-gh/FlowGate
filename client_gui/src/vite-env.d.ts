/// <reference types="vite/client" />

declare global {
  interface Window {
    __accessToken__?: string
    __userPermissions__?: {
      is_admin: boolean
      roles: string[]
    }
  }
}

export {}
