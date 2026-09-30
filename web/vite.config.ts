import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 生产由 FastAPI 静态托管 dist，使用相对路径 base 以兼容任意挂载前缀。
export default defineConfig({
  base: './',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 4000,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // 联调：npm run dev 时把 API/WS 代理到 B/S 后端（python -m src.main）
      '/api': { target: 'http://127.0.0.1:16667', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:16667', ws: true },
    },
  },
})
