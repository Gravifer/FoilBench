import tailwindcss from "@tailwindcss/vite";
import {svelte} from "@sveltejs/vite-plugin-svelte";
import {resolve} from "node:path";
import {defineConfig, loadEnv} from "vite";

export default defineConfig(({mode}) => {
  const environment = loadEnv(mode, import.meta.dirname, "FOILBENCH_");
  return {
    root: import.meta.dirname,
    base: environment["FOILBENCH_BASE"] ?? "/",
    publicDir: resolve(import.meta.dirname, "public"),
    plugins: [svelte({configFile: resolve(import.meta.dirname, "svelte.config.js")}), tailwindcss()],
    server: {
      fs: {allow: [resolve(import.meta.dirname, "../..")]},
      host: "127.0.0.1",
      port: 4175,
      strictPort: true,
    },
    preview: {host: "127.0.0.1", port: 4176, strictPort: true},
    build: {outDir: resolve(import.meta.dirname, "dist"), emptyOutDir: true},
  };
});
