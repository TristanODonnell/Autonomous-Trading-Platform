import type { Config } from "tailwindcss";

export default {
    content: ["./index.html", "./src/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                bg: "var(--bg)",
                surface: "var(--surface)",
                surface2: "var(--surface2)",
                border: "var(--border)",
                border2: "var(--border2)",
                text: "var(--text)",
                text2: "var(--text2)",
                text3: "var(--text3)",
                accent: "var(--accent)",
                accent2: "var(--accent2)",
                "accent-dim": "var(--accent-dim)",
                "accent-dim2": "var(--accent-dim2)",
                red: "var(--red)",
                "red-dim": "var(--red-dim)",
                yellow: "var(--yellow)",
                "yellow-dim": "var(--yellow-dim)",
                blue: "var(--blue)",
                "blue-dim": "var(--blue-dim)",
                purple: "var(--purple)",
                "purple-dim": "var(--purple-dim)",
            },
            fontFamily: {
                sans: ["var(--font-sans)", "sans-serif"],
                mono: ["var(--font-mono)", "monospace"],
            },
            borderColor: {
                DEFAULT: "var(--border)",
            },
        },
    },
    plugins: [],
} satisfies Config;
