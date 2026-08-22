import {defineConfig} from "@playwright/test";

export default defineConfig({
  testDir: "tests/browser",
  testMatch: "spa.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  use: {baseURL: "http://127.0.0.1:4176/FoilBench/", headless: true},
  webServer: {
    command: "npx vite preview --config vite.web.config.ts --base /FoilBench/ --host 127.0.0.1 --port 4176 --strictPort",
    url: "http://127.0.0.1:4176/FoilBench/",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
