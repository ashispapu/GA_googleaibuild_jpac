import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

// Light theme only. Status colours are the only strong colours on screen:
// ok = green, warn = amber, crit = red. Brand blue is for actions and links.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1rem" },
    extend: {
      fontFamily: {
        sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
      colors: {
        page: "#F4F6F9",
        surface: "#FFFFFF",
        subtle: "#F7F9FB",
        muted: "#F0F3F7",
        line: { DEFAULT: "#E1E6ED", strong: "#C9D2DD", soft: "#EEF1F5" },
        ink: { DEFAULT: "#16202C", 2: "#2B3644", 3: "#3E4A59", 4: "#4B5767", 5: "#66727F" },
        brand: {
          DEFAULT: "#1D5FD1",
          hover: "#164CA8",
          text: "#1A4FAE",
          soft: "#EAF1FD",
          tint: "#F5F8FE",
          border: "#BFD3F6",
          light: "#8FB1EC",
        },
        ok: { DEFAULT: "#1F9D55", text: "#146C3A", deep: "#0F4F2A", soft: "#E8F6EE", border: "#BFE3CD" },
        warn: { DEFAULT: "#E0A100", text: "#8A5300", deep: "#6B4300", soft: "#FFF6E0", border: "#F2D899" },
        crit: { DEFAULT: "#D93025", text: "#B3261E", deep: "#8C1D16", soft: "#FDECEA", border: "#F5C2BD" },
        // shadcn/ui variable names, so `npx shadcn add …` components fit the theme
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
      },
      borderRadius: { lg: "12px", md: "8px", sm: "6px" },
      boxShadow: {
        card: "0 1px 2px rgba(16, 24, 40, 0.04)",
        pop: "0 24px 64px rgba(16, 24, 40, 0.22)",
        lift: "0 8px 24px rgba(29, 95, 209, 0.12)",
      },
      keyframes: {
        pulseRing: {
          "0%": { boxShadow: "0 0 0 0 rgba(217, 48, 37, 0.35)" },
          "100%": { boxShadow: "0 0 0 10px rgba(217, 48, 37, 0)" },
        },
        flow: { "0%": { left: "0%" }, "100%": { left: "100%" } },
      },
      animation: {
        "pulse-ring": "pulseRing 1.6s ease-out infinite",
        flow: "flow 6s linear infinite",
      },
    },
  },
  plugins: [animate],
};

export default config;
