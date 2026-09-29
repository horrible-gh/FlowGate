/// <reference types="vite/client" />

declare global {
  /** Build date (YYYY.MM.DD) injected by vite.config.ts `define`. */
  const __FG_GUI_BUILD__: string

  interface Window {
    __accessToken__?: string
    __userPermissions__?: {
      is_admin: boolean
      roles: string[]
    }
  }
}

export {}
