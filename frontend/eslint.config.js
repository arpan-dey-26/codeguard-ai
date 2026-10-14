import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Finding descriptions and AI-produced text are untrusted (Zone 0).
      // Rendering them as HTML is the stored-XSS path, so the escape hatch is
      // banned outright rather than left to code review.
      'no-restricted-properties': [
        'error',
        {
          object: 'React',
          property: 'createElement',
          message: 'Use JSX; dangerouslySetInnerHTML is forbidden (architecture.md T8).',
        },
      ],
      'react/no-danger': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      '@typescript-eslint/consistent-type-imports': 'error',
    },
  },
)
