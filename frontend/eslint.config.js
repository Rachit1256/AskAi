import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

export default [
  { ignores: ["dist", "node_modules"] },

  // Build config files run in Node, not the browser. Without this, `process.env`
  // in vite.config.js is an undefined global.
  {
    files: ["*.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.node },
    },
    rules: { ...js.configs.recommended.rules },
  },

  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true }, sourceType: "module" },
    },
    settings: { react: { version: "detect" } },
    plugins: {
      react,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,

      // Core ESLint does not understand JSX: without this rule, every component
      // imported and then used only as <Component /> is reported as unused.
      // Enabled on its own rather than via react/recommended, which would also
      // switch on react/prop-types and flag every component in the project.
      "react/jsx-uses-vars": "error",

      "react-refresh/only-export-components": "warn",
      "no-unused-vars": ["error", {
        varsIgnorePattern: "^[A-Z_]",
        argsIgnorePattern: "^_",
      }],
    },
  },
];
