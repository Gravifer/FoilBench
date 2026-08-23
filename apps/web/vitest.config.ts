import {defineConfig} from "vitest/config";

export default defineConfig({test: {exclude: ["tests/spa.spec.ts", "node_modules/**", "dist/**"]}});
