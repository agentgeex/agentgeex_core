import tailwindcssAnimate from "tailwindcss-animate";

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}", "./registry-items/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        "table-head": "hsl(var(--table-head))",
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Navigation shell (dark sidebar) — surface areas A/C
        chrome: {
          DEFAULT: "hsl(var(--chrome))",
          foreground: "hsl(var(--chrome-foreground))",
          hover: "hsl(var(--chrome-hover))",
          active: "hsl(var(--chrome-active))",
          "active-foreground": "hsl(var(--chrome-active-foreground))",
        },
        // Semantic state colours (new code must use semantic tokens, never emerald-*/sky-*/amber-*)
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        info: {
          DEFAULT: "hsl(var(--info))",
          foreground: "hsl(var(--info-foreground))",
        },
        "action-run": {
          DEFAULT: "hsl(var(--action-run))",
          foreground: "hsl(var(--action-run-foreground))",
        },
        "action-view": {
          DEFAULT: "hsl(var(--action-view))",
          foreground: "hsl(var(--action-view-foreground))",
        },
        // Category identity colours (role badges / legends, multi-colour with no semantics)
        chart: {
          1: "hsl(var(--chart-1))",
          2: "hsl(var(--chart-2))",
          3: "hsl(var(--chart-3))",
          4: "hsl(var(--chart-4))",
          5: "hsl(var(--chart-5))",
          6: "hsl(var(--chart-6))",
        },
        // Code / terminal panel (stays dark in both light and dark themes)
        code: {
          DEFAULT: "hsl(var(--code))",
          foreground: "hsl(var(--code-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      // Six-step type scale (semantic names, additive without overriding the default xs/sm/base…)
      fontSize: {
        caption: ["0.75rem", { lineHeight: "1rem" }], // 12px helper / caption
        body: ["0.875rem", { lineHeight: "1.5rem" }], // 14px body baseline
        "body-lg": ["1rem", { lineHeight: "1.5rem" }], // 16px emphasised body
        subtitle: ["1.125rem", { lineHeight: "1.75rem" }], // 18px section heading
        title: ["1.375rem", { lineHeight: "1.875rem" }], // 22px page title
        display: ["1.75rem", { lineHeight: "2.25rem" }], // 28px display title
      },
      // Three shadow tiers (resting / raised / overlay) — never use ad-hoc shadow-[Npx]
      borderColor: { strong: "hsl(var(--border-strong))" },
      boxShadow: {
        // "Raised" is only visible on the grey canvas: one step deeper than shadow-sm
        surface: "0 1px 3px 0 rgb(16 24 40 / 0.08), 0 1px 2px -1px rgb(16 24 40 / 0.06)",
        raised:
          "0 4px 12px -2px rgb(0 0 0 / 0.10), 0 2px 4px -2px rgb(0 0 0 / 0.06)",
        overlay:
          "0 12px 32px -8px rgb(0 0 0 / 0.24), 0 4px 8px -4px rgb(0 0 0 / 0.12)",
      },
      // Fixed z scale (no arbitrary values such as z-[999])
      zIndex: {
        header: "30",
        dropdown: "40",
        overlay: "50",
        modal: "60",
        popover: "70",
        toast: "80",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
      },
    },
  },
  plugins: [tailwindcssAnimate],
};
