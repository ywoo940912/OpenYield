/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        yield: {
          high:   "#22c55e",
          mid:    "#f59e0b",
          low:    "#ef4444",
        },
      },
    },
  },
  plugins: [],
};
