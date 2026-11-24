/**
 * Site storage keys — **single registry** + old-key migration.
 *
 * ## Why a central registry
 * Key names scattered across components means nobody knows which keys the site actually uses; renaming requires
 * guesswork with grep. Here one table states it: what the new key is, what the old key is, when the old key can be
 * deleted.
 *
 * ## Why old-key fallback is mandatory
 * Renaming a key = at the moment the user upgrades, the original draft/filters/token **cannot be read at all**.
 * Fatal for login state especially: after an upgrade a sudden re-login makes people think it broke.
 * So reads always go through `readKey()`: read the new key first, else read the old key and **move** the value to the
 * new key. Old keys are **not deleted** (kept so a rollback to the old version still works), cleaned up together once
 * no one rolls back.
 *
 * ## Naming convention (read this before adding a new key)
 *   agentgeex.<domain>.<purpose>[.vN]
 *   · prefix is only `agentgeex.` (historical `win3.` was the old product name, `agx_web.` was an interim stage)
 *   · bump `.vN` only when the structure changes; a pure rename does not bump the version
 */

/** Key registry: new key → compatible old keys (by priority) */
export const STORAGE_KEYS = {
  /** Login token / user info */
  token: { key: "agentgeex.token", legacy: ["win3.token"] },
  user: { key: "agentgeex.user", legacy: ["win3.user"] },
  /** Message send: unsent drafts (bucketed by recipient). Structure unchanged; renamed and bumped to v2 together to avoid misreading old structure */
  sendDraft: { key: "agentgeex.send-draft.v2", legacy: ["win3.send.draft.v1"] },
  /** Message send: last settings (project / sender & receiver / method ...) */
  sendState: { key: "agentgeex.send.state.v1", legacy: ["agx_web.uib.state.v1"] },
  /** Message send: last sending subject */
  sendSender: { key: "agentgeex.send.sender", legacy: ["agx_web.uib.sender"] },
  /** Quick command dialog: caller Agent */
  quickCmdCaller: { key: "agentgeex.quickcmd.caller", legacy: ["win3.qc.caller"] },
  /** Quick command list: filters */
  agentsFilters: { key: "agentgeex.agents.filters", legacy: ["win3.agents.filters"] },
  /** Settings dialog: last active tab */
  settingsTab: { key: "agentgeex.settings.tab", legacy: ["win3.svc.tab"] },
} as const;

export type StorageKeyName = keyof typeof STORAGE_KEYS;

/**
 * Read a key, **auto-migrating** the old key's value.
 *
 *  @returns the value (null when absent); null also when localStorage is unavailable (private mode)
 */
export function readKey(name: StorageKeyName): string | null {
  const { key, legacy } = STORAGE_KEYS[name];
  try {
    const v = localStorage.getItem(key);
    if (v !== null) return v;
    for (const old of legacy) {
      const ov = localStorage.getItem(old);
      if (ov === null) continue;
      /* Move it over (old key kept: in case of a rollback, that side must still read). */
      try {
        localStorage.setItem(key, ov);
      } catch {
        /* Quota full / private mode: the value is still returned, just not moved successfully */
      }
      return ov;
    }
    return null;
  } catch {
    return null;
  }
}

/** Write a key (does not touch old keys — see "old keys not deleted" in the file header) */
export function writeKey(name: StorageKeyName, value: string): void {
  try {
    localStorage.setItem(STORAGE_KEYS[name].key, value);
  } catch {
    /* Private mode / quota full: losing one write is acceptable; must not throw and interrupt the send */
  }
}

/**
 * Run once at startup: **actively** move all old-key values to the new keys.
 *
 * Why "actively": `readKey` is lazy migration — whoever reads, migrates.
 * But some keys (e.g. user info) may go a whole year without being read by any page, so they never migrate, and on
 * rollback/cleanup you still have to remember "which were moved and which were not". Running once at startup makes
 * the state definite. Cost: a few localStorage reads, negligible.
 */
export function migrateStoredKeys(): void {
  for (const name of Object.keys(STORAGE_KEYS) as StorageKeyName[]) {
    readKey(name);
  }
}

/** Delete a key (new and old together: this is a "user-initiated clear", not a migration) */
export function removeKey(name: StorageKeyName): void {
  const { key, legacy } = STORAGE_KEYS[name];
  try {
    localStorage.removeItem(key);
    for (const old of legacy) localStorage.removeItem(old);
  } catch {
    /* ignore */
  }
}
