import { createLogger, defineConfig, type Logger } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const logFile = path.resolve(__dirname, '../logs/frontend.log');

/** 每次启动覆盖写入 logs/frontend.log，同时保留终端输出。 */
function createFileLogger(): Logger {
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  fs.writeFileSync(logFile, '', 'utf8');
  const sink = fs.createWriteStream(logFile, { flags: 'a', encoding: 'utf8' });
  const base = createLogger();
  const write = (type: string, msg: string) => {
    sink.write(`[${type}] ${msg}\n`);
  };
  return {
    ...base,
    info(msg, options) {
      base.info(msg, options);
      write('info', msg);
    },
    warn(msg, options) {
      base.warn(msg, options);
      write('warn', msg);
    },
    warnOnce(msg, options) {
      base.warnOnce(msg, options);
      write('warn', msg);
    },
    error(msg, options) {
      base.error(msg, options);
      write('error', msg);
    },
  };
}

export default defineConfig({
  plugins: [react()],
  customLogger: createFileLogger(),
  base: '/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    target: 'es2020',
    outDir: 'dist',
    sourcemap: false,
    // modulePreload defaults to preloading every static import in the
    // dependency graph, including chunks routed through dynamic imports
    // because Vite walks the static graph. That made vendor-charts (525 KB)
    // and vendor-xterm (286 KB) ride along on the login page even though
    // only Monitor / DeviceShell actually use them. Strip those two from
    // the preload list — the browser still fetches them when the lazy
    // route loads, just not before login.
    modulePreload: {
      resolveDependencies(_filename, deps) {
        return deps.filter(
          (dep) =>
            !dep.includes('vendor-charts') &&
            !dep.includes('vendor-echarts') &&
            !dep.includes('vendor-xterm'),
        );
      },
    },
    rollupOptions: {
      output: {
        // Split *only* the truly heavy lazy-loaded deps so they don't
        // get pulled into the main bundle. Conservative on purpose:
        // aggressive chunking can break init order for libs with
        // top-level side effects (the symptom is a black page on a
        // route that uses something forced into a separate chunk).
        // recharts and xterm are the two that matter for size; leave
        // react / react-router / zustand / lucide / react-markdown in
        // the default bundle so vite figures the order out.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('echarts') || id.includes('zrender')) {
            return 'vendor-echarts';
          }
          if (id.includes('recharts') || id.includes('d3-') || id.includes('victory-')) {
            return 'vendor-charts';
          }
          if (id.includes('xterm')) {
            return 'vendor-xterm';
          }
          return undefined;
        },
      },
    },
  },
  server: {
    // 显式绑 IPv4，避免只听 ::1 导致 http://127.0.0.1:5173 连不上
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18080',
        changeOrigin: true,
        // SSE 长连接：禁止代理超时掐断流
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
});
