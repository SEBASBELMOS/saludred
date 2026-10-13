import plugin from "tailwindcss/plugin";

// Durations in ms. Read from CSS variables by motionDuration() in components/motion.tsx.
const motion = {
  press: 160,
  tip: 150,
  exit: 150,
  base: 200,
  drawer: 280,
  toast: 7000,
};
const ease = {
  out: "cubic-bezier(0.23, 1, 0.32, 1)",
  "in-out": "cubic-bezier(0.77, 0, 0.175, 1)",
  drawer: "cubic-bezier(0.32, 0.72, 0, 1)",
};
// A state colour comes with a light tint for backgrounds and a dark tone for text on that tint.
const tone = (main, tint, ink) => ({ DEFAULT: main, tint, ink });
// Only the start is declared: the cell fades back to its own background colour.
const flash = { from: { backgroundColor: "#B8DCD5" } };
export default {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    "./node_modules/primereact/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      white: "#FFFFFF",
      ink: { DEFAULT: "#17262C", muted: "#4A5C62" },
      canvas: "#F2F5F4",
      surface: { DEFAULT: "#FFFFFF", sunken: "#E8EDEC" },
      line: { DEFAULT: "#D5DDDB", strong: "#9DADA9" },
      accent: { DEFAULT: "#126E62", strong: "#0D5149", tint: "#E1F0ED" },
      bed: {
        available: tone("#1E7A46", "#E2F2E8", "#165C35"),
        occupied: tone("#3D5A73", "#E4EAF0", "#2F4659"),
        cleaning: tone("#A06A12", "#F8EEDA", "#7D5209"),
        reserved: tone("#5B4B8A", "#ECE8F5", "#463872"),
        blocked: tone("#7D898D", "#EAEEEF", "#4A5C62"),
        maintenance: tone("#7D898D", "#EAEEEF", "#4A5C62"),
      },
      critical: tone("#B42318", "#FCEBE9", "#8E1B12"),
      urgent: tone("#B5541C", "#FBEBDF", "#86400F"),
    },
    fontSize: {
      xs: ["12px", "16px"],
      meta: ["13px", "18px"],
      sm: ["14px", "20px"],
      base: ["16px", "24px"],
      lg: ["19px", "26px"],
      xl: ["23px", "28px"],
      "2xl": ["28px", "32px"],
      // One extra step, used only by the login headline (the one place a large
      // statement balances against a full-height brand panel). Never in the app UI.
      display: ["36px", "40px"],
    },
    borderRadius: {
      none: "0",
      sm: "4px",
      DEFAULT: "6px",
      md: "6px",
      lg: "6px",
      xl: "10px",
      "2xl": "10px",
      full: "9999px",
    },
    // Only floating surfaces (dialogs, popovers, drawer, toasts) cast a shadow.
    boxShadow: {
      none: "none",
      overlay:
        "0 12px 32px -8px rgb(23 38 44 / 0.28), 0 2px 8px -2px rgb(23 38 44 / 0.12)",
    },
    extend: {
      transitionDuration: Object.fromEntries(
        Object.entries(motion).map(([k, v]) => [k, `${v}ms`]),
      ),
      transitionTimingFunction: {
        out: "var(--ease-out)",
        "in-out": "var(--ease-in-out)",
        drawer: "var(--ease-drawer)",
      },
      keyframes: { flash, tip: { from: { opacity: "0" } } },
      animation: {
        flash: "flash 1s linear",
        "flash-again": "flash 1s linear",
        tip: "tip 150ms var(--ease-out)",
      },
      fontFamily: {
        sans: ['"Source Sans 3"', "sans-serif"],
        display: ['"Barlow Semi Condensed"', "sans-serif"],
      },
    },
  },
  plugins: [
    plugin(({ addBase, addVariant, addUtilities }) => {
      addBase({
        ":root": {
          "--ease-out": ease.out,
          "--ease-in-out": ease["in-out"],
          "--ease-drawer": ease.drawer,
          ...Object.fromEntries(
            Object.entries(motion).map(([key, value]) => [
              `--motion-${key}`,
              String(value),
            ]),
          ),
        },
      });
      // Hover only where a real pointer exists (never on touch).
      addVariant(
        "hov",
        "@media (hover: hover) and (pointer: fine) { &:hover }",
      );
      addUtilities({
        // Diagonal hatch for beds that are out of service (blocked, maintenance).
        // Used by the segmented strip; tiles use the dark variant over their tint.
        ".bg-hatch": {
          backgroundImage:
            "repeating-linear-gradient(135deg, rgb(255 255 255 / 0.7) 0 2px, transparent 2px 6px)",
        },
        ".bg-hatch-dark": {
          backgroundImage:
            "repeating-linear-gradient(135deg, rgb(74 92 98 / 0.2) 0 1.5px, transparent 1.5px 5px)",
        },
      });
    }),
  ],
};
