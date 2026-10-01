import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  {
    ignores: [".next/**", "node_modules/**", "e2e/.results/**", "src/lib/api-schema.d.ts", "next-env.d.ts"],
  },
  {
    rules: {
      // Server-sanitized HTML is rendered deliberately through <Prose>; images are user uploads served by the API.
      "@next/next/no-img-element": "off",
      // Hydration-safe patterns (reading localStorage after mount, syncing forms from fetched data) set state in effects.
      "react-hooks/set-state-in-effect": "off",
      // Relative-time helpers read the clock during render; values refresh on re-render/refetch, which is intended.
      "react-hooks/purity": "warn",
      // Apostrophes in JSX text render correctly in React; escaping them hurts readability of UI copy.
      "react/no-unescaped-entities": "off",
    },
  },
];

export default config;
