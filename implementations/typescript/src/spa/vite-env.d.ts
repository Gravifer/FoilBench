/// <reference types="vite/client" />

declare module "*.json?url" {
  const url: string;
  export default url;
}
