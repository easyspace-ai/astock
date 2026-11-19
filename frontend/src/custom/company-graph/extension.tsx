import { lazy } from 'react'
import type { FrontendExtension, FrontendSlotContextMap } from '@/extensions/types'
import { Network } from 'lucide-react'

export const CompanyGraphPage = lazy(() => import('./Page').then(m => ({ default: m.CompanyGraphPage })))

/** 个股详情对话框 tab: 轻量图谱视图 (symbol 与后端标准代码同形, 直接透传) */
function StockGraphTab({ symbol }: FrontendSlotContextMap['stock-preview.tabs']) {
  return (
    <div className="max-h-[60vh] overflow-y-auto p-4">
      <CompanyGraphDetailLazy symbol={symbol} />
    </div>
  )
}

const CompanyGraphDetailLazy = lazy(() => import('./CompanyDetail').then(m => ({ default: m.CompanyDetail })))

const extension: FrontendExtension = {
  id: 'official.company-graph',
  apiVersion: 1,
  routes: [
    { id: 'company-graph', path: '/company/graph', component: CompanyGraphPage },
  ],
  navigation: [
    { id: 'company-graph', routeId: 'company-graph', label: '公司图谱', icon: Network },
  ],
  slots: [
    {
      name: 'stock-preview.tabs',
      id: 'company-graph-stock-tab',
      label: '公司图谱',
      order: 90,
      component: StockGraphTab,
    },
  ],
}

export default extension
