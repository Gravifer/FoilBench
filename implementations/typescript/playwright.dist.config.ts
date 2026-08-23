import {defineConfig} from "@playwright/test";

export default defineConfig({
  testDir: "tests/browser",
  testMatch: "viewer.spec.ts",
  fullyParallel: false,
  workers: 1,
  use: {baseURL: "http://127.0.0.1:4174", headless: true},
  webServer: {
    command: "npx vite preview --host 127.0.0.1 --port 4174 --strictPort",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
