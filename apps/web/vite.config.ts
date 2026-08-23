import tailwindcss from "@tailwindcss/vite";
import {svelte} from "@sveltejs/vite-plugin-svelte";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {defineConfig, loadEnv} from "vite";

import {createReleaseMetadata} from "./releaseMetadata.js";

export default defineConfig(({mode}) => {
  const environment = loadEnv(mode, import.meta.dirname, "FOILBENCH_");
  const base = environment["FOILBENCH_BASE"] ?? "/";
  const contractPath = resolve(import.meta.dirname, "../../spec/contract-version.json");
  const contractValue: unknown = JSON.parse(readFileSync(contractPath, "utf8"));
  if (
    typeof contractValue !== "object"
    || contractValue === null
    || !("contract_id" in contractValue)
    || typeof contractValue.contract_id !== "string"
    || !("revision" in contractValue)
    || typeof contractValue.revision !== "number"
  ) throw new Error(`invalid contract identity in ${contractPath}`);
  const releaseMetadata = createReleaseMetadata(
    {
      version: environment["FOILBENCH_RELEASE_VERSION"],
      commit: environment["FOILBENCH_RELEASE_COMMIT"],
      builtAt: environment["FOILBENCH_RELEASE_BUILT_AT"],
    },
    {contractId: contractValue.contract_id, revision: contractValue.revision},
    base,
  );
  return {
    root: import.meta.dirname,
    base,
    publicDir: resolve(import.meta.dirname, "public"),
    plugins: [
      svelte({configFile: resolve(import.meta.dirname, "svelte.config.js")}),
      tailwindcss(),
      {
        name: "foilbench-release-metadata",
        generateBundle() {
          this.emitFile({
            type: "asset",
            fileName: "release.json",
            source: `${JSON.stringify(releaseMetadata, null, 2)}\n`,
          });
        },
      },
    ],
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
