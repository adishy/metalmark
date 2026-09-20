/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // MetalMark palette (slate base, teal accent)
        brand: {
          DEFAULT: "#14b8a6",
          fg: "#0f766e",
        },
      },
    },
  },
  plugins: [],
};
