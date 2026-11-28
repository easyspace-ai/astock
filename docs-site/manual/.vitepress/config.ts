import { defineConfig } from 'vitepress'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const manifest = JSON.parse(fs.readFileSync(path.join(here, '../scripts/manifest.json'), 'utf8')) as {
  groups: Array<{ title: string; items: Array<{ id: string; label: string }> }>
  sections: Array<{ id: string; file: string; title: string; chapter: boolean }>
}

const fileById = new Map(manifest.sections.map(s => [s.id, s.file]))

/** sidebar 由旧版导航结构生成（scripts/manifest.json），新增章节时同步更新清单。 */
const sidebar = manifest.groups.map(group => ({
  text: group.title,
  items: group.items.map(item => {
    const file = fileById.get(item.id)
    if (!file) throw new Error(`manifest 缺少章节 ${item.id}`)
    return { text: item.label, link: file === 'index.md' ? '/' : '/' + file.replace(/\.md$/, '') }
  }),
}))

export default defineConfig({
  lang: 'zh-CN',
  title: '财富引擎交付说明书',
  description: 'A 股智能量化工作台 · 访问方式 / 使用说明 / 注意事项 / 字段说明',
  // 线上部署在后端 /manual 下（README 有说明）；本地 dev 走根路径。
  // 构建线上产物：DOCS_BASE=/manual/ npm run build
  base: process.env.DOCS_BASE || '/',
  head: [['link', { rel: 'icon', type: 'image/svg+xml', href: `${process.env.DOCS_BASE || '/'}favicon-mark.svg` }]],
  srcExclude: ['scripts/**', 'node_modules/**', 'dist-print/**', 'README.md'],
  cleanUrls: true,
  lastUpdated: false,
  markdown: {
    image: { lazyLoading: true },
    theme: { light: 'github-light', dark: 'github-dark' },
  },
  themeConfig: {
    logo: '/logo-mark.svg',
    siteTitle: '财富引擎 · 交付说明书',
    outline: { level: [2, 3], label: '本页目录' },
    nav: [
      { text: '交付概览', link: '/' },
      { text: '使用说明', link: '/modules/dash' },
      { text: '字段说明', link: '/fields' },
    ],
    sidebar,
    search: { provider: 'local' },
    docFooter: { prev: '上一节', next: '下一节' },
    darkModeSwitchLabel: '深色模式',
    returnToTopLabel: '回到顶部',
    sidebarMenuLabel: '目录',
    lastUpdatedText: '最后更新',
    footer: {
      message: '本文档仅限接收方内部使用；策略结果与 AI 报告均为研究性质材料，不构成投资建议。',
      copyright: '财富引擎 · 交付说明书 V1.0 · 产品版本 v0.3.36',
    },
  },
})
