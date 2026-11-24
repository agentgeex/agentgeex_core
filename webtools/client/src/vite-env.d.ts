/// <reference types="vite/client" />

declare module "*.json" {
  const value: any;
  export default value;
}

/** [2026-10-20] Build fingerprint (injected by vite.config.ts define), shown in the footer to tell whether the front end is current. */
declare const __BUILD_STAMP__: string;
