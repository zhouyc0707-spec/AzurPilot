/**
 * @fileoverview Vite 配置：dev 代理指向本机后端（mock 模式指向 mock 服务），构建产物用相对 base 以适配远程反代。
 */

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({mode}) => {
  const backend = mode === 'mock' ? `http://127.0.0.1:${process.env.AZURPILOT_MOCK_PORT ?? 22392}` : process.env.AZURPILOT_BACKEND ?? 'http://127.0.0.1:22267'
  return {
    // 相对构建：远程访问经前缀式反代（/<peer_id>/）加载时，绝对路径 /assets 会打到
    // 隧道服务端而 404/502；相对路径才能命中反代前缀。
    // SSH、Cloudflare 及公网 HTTP 均按实际入口前缀加载资源。P2P 使用独立数据通道，
    // 页面入口由远程访问服务选择可用的 HTTP 通道，无需在构建阶段限定访问模式。
    base: './',
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': { target: backend, ws: true },
        '/healthz': { target: backend },
        '/gui-icons': { target: backend },
        // 物品图标走后端的静态目录（module/api/statistics_service.py 挂的
        // research-items / opsi-items），开发时也由同一份后端提供，
        // 不代理的话统计页的图标列全是裂图。
        '/research-items': { target: backend },
        '/opsi-items': { target: backend },
        '/storage-items': { target: backend },
      },
    },
    build: {
      minify: 'oxc',
      cssMinify: 'lightningcss',
      rolldownOptions: {
        output: {
          codeSplitting: {
            groups: [{
              name: 'react-runtime',
              test: /[/\\]node_modules[/\\](?:react|react-dom|react-router|react-router-dom|scheduler)[/\\]/,
            }],
          },
        },
      },
    },
  }
})
