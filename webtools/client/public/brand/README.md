<!-- English (GitHub) primary. Chinese version: README.zh-CN.md -->

# brand/ — AgentGeeX brand image directory

Every **brand / static image** in the frontend lives here (`public/brand/`, copied verbatim to the site root `/brand/` after build).
Images added later (logo variants, empty-state illustrations, partner marks, etc.) also go in this directory — stop scattering them into `public/` root.

## ⚠️ SVG is the source of truth, every PNG is a build product

```
logo-mark.svg        ← the only source of truth (edit images here and nowhere else)
   └─ node scripts/brand_export.mjs --ico
        ├─ logo-mark.png 512 / logo-64.png / apple-touch-icon.png 180
        ├─ favicon-32.png / favicon-16.png / favicon.ico(16·32·48·64)
```

Hand-editing a PNG creates a second source of truth: next time someone edits the SVG the PNG is stale, and **nobody can tell**.
So edit only the SVG, then run the script once and every size is regenerated together (the script spells out each size).

### The AGX mark (reworked by the author, 2026-11-10)

In the author's own words: "This icon isn't great — the design wasn't explained clearly back then… AGX, that's
        three letters, just put the three letters in it,
        no need to overcomplicate it. … What we have now actually looks worse."

| File | Description |
|---|---|
| `logo-mark.svg` | **Combined mark (in use)**: G·X on the top row + one large A below, solid rounded square + white single-stroke letters |
| `logo-wordmark.svg` | Alternative: AGX side by side (glyph bodies fill only 33% of the square's height, degrading to a thin band at 24px) |

Why the combined mark over three letters in a row: inside a square icon each of three letters is only 1/3 as wide,
**at sidebar size 24–28px it collapses into a 20×8 smudge**; the combined mark fills the square with ink (19×19 at 24px).

Every glyph is a **hand-written geometric path** (no font dependency): a font dependency renders differently on someone else's machine,
and a logo must not break just because a certain font size isn't installed.

### Spec (finalised by the author 2026-11-10 23:57 · lowercase-g version)

| Item | Value | In his own words |
|---|---|---|
| Ground colour | **blue `#2563EB`** | "Let's change it to blue after all" (the earlier navy is void) |
| A | untouched | "Leave the A alone" |
| Left letter | **lowercase g** (full-circle bowl + right-side stem + downward hook) | "Try lowercase g instead of G — at uppercase G you can't tell it's a G" |
| Right letter | uppercase X (he didn't mention it, unchanged) | |

#### Why lowercase g reads better than uppercase G (measured, not felt)

Uppercase G is built as "a ring + **an inner** horizontal bar". At 24px the ring interior is only 3px,
and a 1.3px bar crosses it → the counter is cut in two, each half under 1px, and anti-aliasing flattens it to **zero**:
measured closed-counter count for uppercase G at 24px = **0** (i.e. a small circle with a notch).

Lowercase g is built as "a round bowl + **one tail outside** the ring".
The tail sits outside the ring and doesn't compete with the counter → counter count at 24px = **4**, at 28px = **8**.
Plus the vertical asymmetry created by the descender, which the eye reads more readily as a letter rather than an ornament.

#### Typographic detail (lowercase is not "a shrunken uppercase")

- g's **top sits below X's top** (32 vs 28) — that's x-height; lowercase is supposed to be shorter than uppercase
- g's **bottom sits below X's bottom** (157 vs 147) — the descender extends past the baseline; that is what identifies a lowercase g
- g's **bottom still sits above the A** (157 < the A apex stroke 157..) — it does not crowd the main letter

#### Final form of the lowercase g (finalised by the author 2026-11-11 07:58)

In his own words: "This is very good already, just one thing — that g's tail is a bit short, extend it a little further
           to the left, otherwise it looks like a 9; and the right-hand vertical of the g, let it curve up a bit at the top.
           Right now it does look like a 9."

| Item | Previous version | Final | Note |
|---|---|---|---|
| Hook radius | 32 | **42** | The hook itself is larger |
| Hook angle | 112° | **115°** | The end curls further back to the upper left |
| Hook horizontal span | 44 | **60** (+36%) | This is the measured version of "extend to the left" |
| Tail exposure (descender) | 67 | **75** | |
| Stem top | level with the circle centre | **20px above** | What he asked for as "curve up" |

**Why these two changes create distance from "9"**: the digit 9 is built as "a circle + one straight vertical on the right",
with the vertical's top flush against the circle and its bottom going straight down or bending right. The lowercase g's two features are the exact opposite —
the tail curls back to the upper left, and the stem top lifts slightly above.
So:
- tail to the left → breaks "straight down"
- stem lifted at the top → breaks the "flush junction"

As a result the vision model's reading at 220px flipped from "more like the digit 9" to "more like a lowercase g";
at 36px it also went from "like a 9" to "more like a g" (though that 20px lift is only 1.4px at small sizes, invisible).

**The A did not move by a single line**: horizontal centring is still at x=256, and ink and baseline are identical across all three sizes.

#### Measured result per size (vision-model reading)

| Size | Result |
|---|---|
| **220px** | Tail curls to the upper left, stem lifts, **reads as a single-storey lowercase g** (before the change it was judged "like a 9") |
| **140px** | Clear, the curl is visible |
| **96px** | Legible, the features are visible |
| 36px (sidebar) | The tail's curl is visible, **overall more like a g**; the stem's 1.4px lift is invisible |
| 28px and below | Only a short vertical dash remains |

## Existing files

| File | Size | Use |
|---|---|---|
| `logo-mark.png` | 512×512 (transparent) | Sidebar / login-page brand icon (large, with 8% padding) |
| `logo-mark.svg` | vector | Same as above, **this is the one actually referenced** (sharp at any DPI) |
| `logo-64.png` | 64×64 (transparent) | Small icon / documentation use |
| `favicon-32.png` / `favicon-16.png` | 32 / 16 | Browser tab icon |
| `favicon.ico` | 16/32/48/64 multi-size | Legacy favicon fallback (Safari, older browsers) |
| `apple-touch-icon.png` | 180×180 | iOS add-to-home-screen |

## Naming conventions

- All lowercase, hyphen-separated: `logo-mark.png`, `empty-agents.svg`
- Multiple sizes of one image: `<name>-<px>.png` (e.g. `logo-64.png`)
- Original assets go in `source/`, never referenced by the build output
- Prefer **transparent PNG** or **SVG** (works on both dark and light themes); never ship a white-background image

## Provenance

`source/agentgeex-original-1024.png` = the original image the author supplied on 2026-10-19 (1024×1024, with a lot of white margin).
Processing: flood-fill the white from all four edges → auto-crop to content (497×411) → square with padding → export every size.

## avatars/ — object avatars (Agent / assistant)

- Naming: **filename = object id**, assistants carry a `case_` prefix (e.g. `aifr_pm.png`, `case_aifr_intel.png`)
- Source: aifr_pm's `assistants/t2i_research/outputs/logos_final` (finalised 2026-10-19, 1024×1024 on pure white)
- Resolver code: `src/lib/brand-avatars.ts` (an explicit list; no more guessing by path concatenation)
- Fallback chain: `avatarUrl` (backend `agent_avatar`) → local brand image → initial letter + deterministic colour
- Adding an object: drop the image in this directory and add one line to `BRAND_AVATARS` (anything not listed falls back to the initial letter)

| File | Object | Primary colour |
|---|---|---|
| `aifr_pm.png` | aifr_pm (AI frontier radar PM) | `#1F5FA8` |
| `case_aifr_intel.png` | aifr_intel | `#E07B39` |
| `case_agent_research.png` | agent_research | `#6C5CE7` |
| `case_skill_research.png` | skill_research | `#2E9E5B` |
| `case_t2i_research.png` | t2i_research | `#D6336C` |
| `case_anim_video_research.png` | anim_video_research | `#E03E3E` |
| `case_jev_research.png` | jev_research | `#12A594` |
| `case_a320_edu.png` | a320_edu | `#2B7CD3` |
| `case_zeroclaw_research.png` | zeroclaw_research | `#37474F` |
