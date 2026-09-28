/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API 基地址；本地留空走 vite proxy（/api -> 127.0.0.1:8010） */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
