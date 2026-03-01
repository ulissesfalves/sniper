import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ── Institutional dark palette ────────────────────────────────────
        surface: {
          900: '#080c10',   // deepest bg
          800: '#0d1117',   // main bg
          700: '#161b22',   // card bg
          600: '#1c2230',   // panel bg
          500: '#222b3a',   // hover state
          400: '#2d3748',   // border
        },
        primary: {
          DEFAULT: '#3b82f6',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
        },
        // Signal colors (P&L, risk)
        bull:   '#10b981',   // green — profit / bull signal
        bear:   '#ef4444',   // red   — loss / bear signal / halt
        warn:   '#f59e0b',   // amber — moderate drift / caution
        info:   '#6366f1',   // indigo — neutral info
        muted:  '#4b5563',   // gray  — secondary text
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.65rem', { lineHeight: '1rem' }],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'blink': 'blink 1s step-start infinite',
      },
      keyframes: {
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%':       { opacity: '0' },
        },
      },
    },
  },
  plugins: [],
}

export default config
