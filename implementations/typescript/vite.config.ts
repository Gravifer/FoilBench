import {defineConfig} from "vite";
import {resolve} from "node:path";

export default defineConfig({
  root: resolve(import.meta.dirname),
  server: {fs: {allow: [resolve(import.meta.dirname, "../..")]}, port: 4173},
  build: {
    outDir: "dist-viewer", emptyOutDir: true,
    rollupOptions: {input: {viewer: resolve(import.meta.dirname, "index.html"), benchmark: resolve(import.meta.dirname, "benchmark.html")}},
  },
});
