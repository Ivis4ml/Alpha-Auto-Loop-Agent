import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 构建产物输出到 gui/dist，由后端从根路径托管；base 使用相对路径，
// 保证资源引用不依赖部署路径。
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        // 第三方库拆分为独立分块：业务代码变更时不必重新下载图表库。
        manualChunks: {
          echarts: ["echarts/core", "echarts/charts", "echarts/components", "echarts/renderers"],
          react: ["react", "react-dom"],
        },
      },
    },
  },
});
