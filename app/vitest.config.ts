// Vitest config for the frontend tests.
//
// The frontend test(s) live at app/frontend/__tests__/. Test tooling (vitest) is
// resolved via `npx` (as the repo's validation harness does); the React / ReactDOM
// deps they import resolve from app/frontend/node_modules. This config anchors the
// project root at the subproject root (one level up from this `app/` dir) so a test
// referenced by its subproject-relative path (e.g. "app/frontend/__tests__/...") is
// discovered when vitest is invoked from here.
export default {
  root: '..',
  esbuild: {
    jsx: 'automatic' as const,
  },
  test: {
    include: ['app/frontend/__tests__/**/*.{test,spec}.{ts,tsx}'],
    environment: 'node',
  },
};
