/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the backend. Empty in development (dev-server proxy). */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
