import path from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, path.resolve(__dirname, '..'), 'BB_')
  const backendTarget = env.BB_DEV_BACKEND_URL || 'http://127.0.0.1:8000'
  const apiPrefix = env.BB_API_PREFIX || '/api/v1'
  return {
    base: '/dashboard/',
    plugins: [
      react(),
      VitePWA({
        registerType: 'autoUpdate',
        includeAssets: ['icons/icon-192.png', 'icons/icon-512.png'],
        manifest: {
          name: 'FloCast',
          short_name: 'FloCast',
          description: 'FloCast — экспериментальный прогноз годовой линии, синтетические примеры и сценарный калькулятор',
          theme_color: '#F4F5FA',
          background_color: '#F4F5FA',
          display: 'standalone',
          lang: 'ru',
          start_url: './',
          icons: [
            { src: 'icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
            { src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
          ],
        },
        workbox: {
          globPatterns: ['**/*.{js,css,html,woff2,jpg,png,webmanifest}'],
          navigateFallback: undefined,
          runtimeCaching: [
            {
              urlPattern: ({ request }) => request.mode === 'navigate',
              handler: 'NetworkOnly',
              options: { fetchOptions: { cache: 'no-store' } },
            },
          ],
        },
      }),
    ],
    server: {
      port: 5173,
      strictPort: true,
      proxy: Object.fromEntries(
        ['/capacity', '/app.js', '/config.json', apiPrefix].map((route) => [route, { target: backendTarget }]),
      ),
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.replaceAll('\\', '/').endsWith('/geography-catalog.json')) return 'geography-catalog'
          },
        },
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
  }
})
