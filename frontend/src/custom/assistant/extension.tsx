/**
 * AI 对话助手前端扩展 — 完全解耦模块。
 *
 * 注册独立路由 /assistant 与侧栏导航项「AI 助手」: 页面双栏, 左侧历史会话,
 * 右侧对话区。常驻宿主(AssistantHost)只负责页面上下文上报与 ⌘K 快捷键跳转,
 * 不渲染悬浮球/抽屉。删除本目录即整体卸载, 不修改任何核心文件;
 * 复用的核心能力均为只读 import(MarkdownRenderer、cn、设计令牌)。
 * 自包含 HTTP 客户端见 client.ts 的取舍说明。
 */
import { lazy } from 'react'
import { MessagesSquare } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { AssistantHost } from './AssistantHost'

const AssistantPage = lazy(() => import('./ui/AssistantPage').then(m => ({ default: m.AssistantPage })))

const extension: FrontendExtension = {
  id: 'assistant.chat',
  apiVersion: 1,
  routes: [
    { id: 'assistant', path: '/assistant', component: AssistantPage },
  ],
  navigation: [
    { id: 'assistant', routeId: 'assistant', label: 'AI 助手', icon: MessagesSquare, order: 70 },
  ],
  slots: [
    {
      name: 'layout.navigation.extra',
      id: 'assistant-host',
      order: 10,
      component: AssistantHost,
    },
  ],
}

export default extension
