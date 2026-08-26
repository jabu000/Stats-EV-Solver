/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 950: '#080b12', 900: '#0d1220', 850: '#121a2b', 800: '#18223a', 700: '#243150', 600: '#33436b' },
        edge: { pos: '#34d399', neg: '#f87171', warn: '#fbbf24' },
      },
      fontFamily: { mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'] },
    },
  },
  plugins: [],
}
