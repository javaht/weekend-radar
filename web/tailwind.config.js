/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 基础层级：纯白画布 + 极浅灰承托面
        bg: {
          pure: '#ffffff',
          elevated: '#ffffff',
          surface: '#f8f9fa',
          subtle: '#f1f3f5',
        },
        // 细分隔线（发丝线）
        border: {
          hairline: '#e5e7eb',
          specular: '#d1d5db',
          strong: '#9ca3af',
        },
        // 文本层级
        text: {
          primary: '#111111',
          secondary: '#4b5563',
          tertiary: '#6b7280',
          muted: '#9ca3af',
        },
        // 语义化命名（新组件优先使用）
        ink: {
          DEFAULT: '#111111',
          soft: '#374151',
        },
        muted: {
          DEFAULT: '#6b7280',
          soft: '#9ca3af',
        },
        hairline: '#e5e7eb',
        surface: {
          DEFAULT: '#ffffff',
          soft: '#f8f9fa',
          subtle: '#f1f3f5',
        },
        accent: {
          DEFAULT: '#e02424', // 价格红
          soft: '#fee2e2',
          green: '#16a34a',  // 免费 / PASS
          amber: '#b45309',  // 提醒
          blue: '#2563eb',   // 信息
          purple: '#7c3aed',
        },
      },
      fontFamily: {
        sans: [
          '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"PingFang SC"',
          '"Hiragino Sans GB"', '"Microsoft YaHei"', '"Helvetica Neue"', 'Arial', 'sans-serif'
        ],
        mono: [
          '"SF Mono"', '"JetBrains Mono"', 'ui-monospace', 'Menlo', 'monospace'
        ]
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.22, 1, 0.36, 1)',
      }
    },
  },
  plugins: [],
}
