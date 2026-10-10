import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // 股票、心智单元及单次任务用例依赖对应的专用 mock 配置。
  testIgnore: ['**/mock.spec.ts', '**/stock-exchange/**', '**/legacy-single-task.spec.ts', '**/mind-calculator.spec.ts'],
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60000,
  use: {baseURL: 'http://127.0.0.1:22391', headless: true, locale: 'zh-CN', viewport: {width: 1440, height: 1100}},
  webServer: {
    command: 'uv run python -m tests.serve_frontend',
    cwd: '..',
    url: 'http://127.0.0.1:22391/healthz',
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
})
