import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, projectRoot, '')
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      host: env.VITE_DEV_HOST || '127.0.0.1',
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: false,
        },
      },
    },
  }
})
