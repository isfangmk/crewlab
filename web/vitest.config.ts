import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

// vitest piggy-backs on vite.config.ts so the @/ alias and React plugin
// stay in sync with `npm run dev` / `npm run build`. Test-only knobs go
// in the `test` block below.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      // 慢机上 jsdom 全量 transform 会把单文件渲染拖过默认 1 秒：
      // testing-library 的 findByRole 全在等这个窗口，超时后表现为
      // 「找不到元素」的假失败，而不是真断言错误。
      testTimeout: 15000,
      hookTimeout: 15000,
      environment: './src/test/fetch-jsdom-environment.ts',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      css: false,
    },
  }),
);
