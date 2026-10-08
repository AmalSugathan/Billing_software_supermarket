import js from '@eslint/js';
import globals from 'globals';
import hooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist/**', 'coverage/**', 'test-results/**', 'playwright-report/**'] },
  js.configs.recommended,
  { files: ['playwright.config.ts', 'e2e/*.ts'], languageOptions: { globals: globals.node } },
  { files: ['sw-template.js'], languageOptions: { globals: globals.serviceworker } },
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': hooks },
    rules: hooks.configs.recommended.rules,
  },
);
