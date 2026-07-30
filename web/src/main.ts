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

void useJobStore().bootstrap()
