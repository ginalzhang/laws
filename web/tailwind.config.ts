import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      colors: {
        ink: '#1c1c1e',
        muted: '#6e6e73',
        line: '#e5e5ea',
      },
    },
  },
  plugins: [],
} satisfies Config;
