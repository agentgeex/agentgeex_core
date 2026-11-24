/**
 * Pseudo-localization — a **layout stress test** for i18n.
 *
 * ## What problem it solves
 * A short Chinese label can be twice as wide in English (48px for the original → 130px for "Quick commands").
 * Finding out that buttons / navigation / table headers blew up only after all the English is written is the most expensive rework:
 * change the copy or change the layout? Nobody wants to touch either.
 *
 * So first build a fake language `en-XA` that is **longer than real English**:
 *   · every string is wrapped in ⟦ ⟧ (instantly recognizable as fake, not a missing translation)
 *   · length is stretched to about 2.2× (real English rarely exceeds 1.7×)
 *   · interpolation placeholders `{{count}}` are preserved verbatim (otherwise i18next breaks)
 * If the pseudo build holds, the real English will hold.
 *
 * ## Why CJK becomes x while ASCII is kept
 * CJK glyphs are twice as wide as Latin letters; keeping them would "look the same length" and measure nothing.
 * Mapping to `x` gets close to the real character width of English. Keeping ASCII and adding diacritics is the
 * pseudo-locale convention, and it doubles as a check for "what happens when a glyph is missing from the font".
 */

/** ASCII letters → look-alike letters with diacritics (exposes missing glyphs / line-break issues at once) */
const ACCENT: Record<string, string> = {
  a: "á", b: "ƀ", c: "ç", d: "ð", e: "é", f: "ƒ", g: "ĝ", h: "ĥ", i: "í", j: "ĵ",
  k: "ķ", l: "ļ", m: "ɱ", n: "ñ", o: "ó", p: "þ", q: "ǫ", r: "ŕ", s: "š", t: "ţ",
  u: "ú", v: "ṽ", w: "ŵ", x: "ẋ", y: "ý", z: "ž",
  A: "Á", B: "Ɓ", C: "Ç", D: "Ð", E: "É", F: "Ƒ", G: "Ĝ", H: "Ĥ", I: "Í", J: "Ĵ",
  K: "Ķ", L: "Ļ", M: "Ṁ", N: "Ñ", O: "Ó", P: "Þ", Q: "Ǫ", R: "Ŕ", S: "Š", T: "Ţ",
  U: "Ú", V: "Ṽ", W: "Ŵ", X: "Ẋ", Y: "Ý", Z: "Ž",
};

/** Interpolation placeholders: must be preserved verbatim, otherwise i18next cannot substitute them */
const PLACEHOLDER = /(\{\{[^}]+\}\})/g;

/** One string → pseudo text */
export function pseudoizeString(s: string): string {
  return s
    .split(PLACEHOLDER)
    .map((part) => (part.startsWith("{{") ? part : wrap(part)))
    .join("");
}

function wrap(s: string): string {
  if (!s.trim()) return s; // Whitespace and empty strings pass through untouched, do not wreck the layout
  const chars = [...s];
  const mapped = chars
    .map((c) => (c.charCodeAt(0) < 128 ? ACCENT[c] ?? c : "x"))
    .join("");
  /* Target visible length: 2.2× (real English is at most ~1.7×), short words get at least +4 characters
     — a two-character label stretched to only 4.4 will not expose button squeezing; English "Send" is 4 characters but wider. */
  const target = Math.max(Math.ceil(s.length * 2.2), s.length + 4);
  const body = mapped.length >= target ? mapped : mapped + "·".repeat(target - mapped.length);
  return `⟦${body}⟧`;
}

/** Whole locale tree → pseudo text (recursive; handles both arrays and strings) */
export function pseudoize<T>(node: T): T {
  if (typeof node === "string") return pseudoizeString(node) as unknown as T;
  if (Array.isArray(node)) return node.map((v) => pseudoize(v)) as unknown as T;
  if (node && typeof node === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) out[k] = pseudoize(v);
    return out as unknown as T;
  }
  return node;
}
