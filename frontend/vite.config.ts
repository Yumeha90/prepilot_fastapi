import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// 本地开发端口 5180：避开 infra 已占用端口
//   3000/3030(Langfuse) 3003/3004 5000(registry) 8080(Jenkins) 8081(k3d LB)
//   8000/8001(后端容器) 19530(Milvus) 5432/6379/5672 等
// 云端由 nginx 托管 dist/，并将 /api 反代到后端容器 8000
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5180,
    strictPort: true,
    host: '127.0.0.1',
    proxy: {
      // 本地直连后端 8010，避免跨域
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 5180,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
})
