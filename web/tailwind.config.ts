import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1a1a2e",
        panel: "#ffffff",
        page: "#f4f5f7",
      },
    },
  },
  plugins: [],
} satisfies Config;
