import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // 配对、股票、舰船数据及单次任务用例依赖各自的专用模拟服务配置。
  testIgnore: ['**/mock.spec.ts', '**/pairing.spec.ts', '**/stock-exchange/**', '**/legacy-single-task.spec.ts', '**/mind-*.spec.ts', '**/fleet-data-workflow.spec.ts'],
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
