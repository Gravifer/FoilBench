import {defineConfig, devices} from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  testMatch: "spa.compat.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 150_000,
  expect: {timeout: 60_000},
  projects: [
    {name: "firefox", use: {...devices["Desktop Firefox"]}},
    {name: "webkit", use: {...devices["Desktop Safari"]}},
  ],
  use: {baseURL: "http://127.0.0.1:4176/FoilBench/", headless: true},
  webServer: {
    command: "npx vite preview --base /FoilBench/ --host 127.0.0.1 --port 4176 --strictPort",
    url: "http://127.0.0.1:4176/FoilBench/",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
