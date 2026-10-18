import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 子路径部署：容器构建时传 VITE_BASE=/shihuaxiang/；本地不传 → 根路径。
// cssMinify 必须关闭：当前压缩链会把标准 backdrop-filter 等声明按 -webkit- 前缀
// “去重”剥离，Chromium 只认无前缀写法 → 全站磨砂玻璃失效（弹窗等显得过于透明）。
// 关闭后双声明都保留（模板产物即如此）；gzip 后体积差异可忽略。
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  build: { cssMinify: false },
  server: { proxy: { '/api': 'http://127.0.0.1:8321' } },
})
