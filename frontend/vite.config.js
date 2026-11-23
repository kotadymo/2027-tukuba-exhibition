import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'mui',
              test: /node_modules[\\/]@mui[\\/]material[\\/]/,
              priority: 10,
              minSize: 1,
            },
            {
              name: 'mui-icons',
              test: /node_modules[\\/]@mui[\\/]icons-material[\\/]/,
              priority: 10,
              minSize: 1,
            },
            {
              name: 'emotion',
              test: /node_modules[\\/]@emotion[\\/]/,
              priority: 10,
              minSize: 1,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
