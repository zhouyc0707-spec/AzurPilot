import {defineConfig} from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'legacy-single-task.spec.ts',
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:22395', headless: true, locale: 'zh-CN',
    viewport: {width: 1440, height: 1100},
  },
  webServer: {
    command: 'uv run python -m tests.serve_legacy_run_once',
    cwd: '..',
    url: 'http://127.0.0.1:22395/healthz',
    reuseExistingServer: false,
    timeout: 30000,
  },
})
