import js from "@eslint/js"
import globals from "globals"
import react from "eslint-plugin-react"
import reactHooks from "eslint-plugin-react-hooks"
import reactRefresh from "eslint-plugin-react-refresh"
import tseslint from "typescript-eslint"
import { defineConfig, globalIgnores } from "eslint/config"

export default defineConfig([
  globalIgnores(["dist"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: { react },
    settings: { react: { version: "detect" } },
    rules: {
      // --- Aligned with @umijs/lint recommended rules ---
      // JS core
      "array-callback-return": "error",
      eqeqeq: ["error", "always"],
      "guard-for-in": "error",
      "no-eval": "error",
      "no-param-reassign": "warn",
      "no-promise-executor-return": "error",
      "no-self-compare": "error",
      "no-with": "error",

      // React
      "react/button-has-type": "error",
      "react/jsx-key": "error",
      "react/jsx-no-comment-textnodes": "error",
      "react/jsx-no-duplicate-props": "error",
      "react/jsx-no-target-blank": "error",
      "react/jsx-no-undef": "error",
      "react/no-children-prop": "error",
      "react/no-danger-with-children": "error",
      "react/no-deprecated": "error",
      "react/no-direct-mutation-state": "error",
      "react/no-find-dom-node": "error",
      "react/no-is-mounted": "error",
      "react/no-render-return-value": "error",
      "react/no-string-refs": "error",
      "react/no-unescaped-entities": "error",
      "react/no-unknown-property": "error",
      "react/require-render-return": "error",

      // TypeScript (supplement tseslint.recommended with @umijs/lint rules)
      "@typescript-eslint/no-confusing-non-null-assertion": "error",
      "@typescript-eslint/no-dupe-class-members": "error",
      "@typescript-eslint/no-invalid-this": "error",
      "@typescript-eslint/no-loop-func": "error",
      "@typescript-eslint/no-redeclare": "error",
      "@typescript-eslint/no-use-before-define": [
        "error",
        { functions: false, variables: true, typedefs: false },
      ],
      "@typescript-eslint/no-useless-constructor": "error",
    },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/components/ui/**", "src/components/dialogs/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@/components/ui/dialog",
              importNames: ["DialogContent"],
              message:
                "Use components/dialogs/DialogContent for shared modal sizing.",
            },
            {
              name: "@/components/ui/alert-dialog",
              importNames: ["AlertDialogContent"],
              message:
                "Use components/dialogs/AlertDialogContent for shared confirmation sizing.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/components/ui/**/*.{ts,tsx}", "src/contexts/**/*.{ts,tsx}"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
  {
    files: ["src/components/ui/**/*.{ts,tsx}"],
    rules: {
      "no-param-reassign": "off",
      "react/button-has-type": "off",
    },
  },
  {
    files: ["src/components/ui/carousel.tsx"],
    rules: {
      // shadcn synchronizes Embla's initial scroll state when the API mounts.
      "react-hooks/set-state-in-effect": "off",
    },
  },
])
