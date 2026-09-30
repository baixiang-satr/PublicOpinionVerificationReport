import { createPinia } from 'pinia'
import { createApp } from 'vue'

import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import { initRealtime } from './api/events'
import { useJobStore } from './stores/job'
import './styles/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')

// B/S 启动引导：先拉 bootstrap（首次 bridge 调用顺带探测 HTTP/mock 传输），
// 再建立 WebSocket 事件通道；连不上后端时 bridge.ts 回退 mock 供纯前端开发。
const start = async () => {
  await useJobStore().bootstrap()
  initRealtime()
}
void start()
