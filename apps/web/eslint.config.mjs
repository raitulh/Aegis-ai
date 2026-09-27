import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const flatten = (c) => (Array.isArray(c) ? c : [c]);

export default [
  ...flatten(coreWebVitals),
  ...flatten(typescript),
  {
    rules: {
      "@next/next/no-img-element": "off",
      "react/no-unescaped-entities": "off",
      "@typescript-eslint/no-explicit-any": "off",
      "react-hooks/set-state-in-effect": "warn",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts", "playwright.config.ts", "vitest.config.ts", "eslint.config.mjs"] },
];
