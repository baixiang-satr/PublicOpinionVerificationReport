import { createPinia } from 'pinia'
import { createApp } from 'vue'

import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import { bridge } from './api/bridge'
import { useJobStore } from './stores/job'
import './styles/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')

// 覆盖 window.open：pywebview 内不弹新窗口，统一走系统默认浏览器。
window.open = (url?: string | URL) => {
  if (url) void bridge.openUrl(String(url))
  return null
}

// 启动引导：pywebview 的 js api 由 api.js 在 navigation completed 后才注入，
// 页面脚本执行时 window.pywebview 尚不存在；直接 bootstrap 会落入 mock
// （mock 许可证=已激活，导致激活页不显示）。WebView2 宿主特征
// window.chrome.webview 在页面加载前即存在，可同步区分宿主与纯浏览器。
const start = () => void useJobStore().bootstrap()
const host = (window as unknown as { chrome?: { webview?: unknown } }).chrome?.webview
if (host) {
  window.addEventListener('pywebviewready', start, { once: true })
} else {
  start() // 纯浏览器 dev：无宿主，直接启动（bridge.ts 走 mock）
}
