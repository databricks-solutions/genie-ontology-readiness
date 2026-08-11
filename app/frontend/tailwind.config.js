/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        databricks: {
          50: '#fff1ee',
          100: '#ffded6',
          200: '#ffc1b3',
          300: '#ff9883',
          400: '#ff6347',
          500: '#FF3621',
          600: '#e62614',
          700: '#bf1c0e',
          800: '#9e1a10',
          900: '#831c14',
        },
        ink: {
          50: '#f3f6f6',
          100: '#dfe7e8',
          200: '#c1d0d2',
          300: '#97afb2',
          400: '#65868a',
          500: '#4a6a6e',
          600: '#3f595e',
          700: '#374b4f',
          800: '#2a393d',
          900: '#1B3139',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
};
