/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        darkTheme: '#0B0E11',
        darkCard: '#161A1E',
        darkRow: '#1E2329',
        lightTheme: '#F3F4F6',
        lightCard: '#FFFFFF',
      },
    },
  },
  plugins: [],
}
