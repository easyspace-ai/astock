import { lazy } from 'react'
import type { FrontendExtension, FrontendSlotContextMap } from '@/extensions/types'
import { Megaphone, Newspaper, Radio } from 'lucide-react'
import { StockInfoTab } from './StockInfoTab'

export const AnnouncementsPage = lazy(() => import('./AnnouncementsPage').then(m => ({ default: m.AnnouncementsPage })))
export const NewsPage = lazy(() => import('./NewsPage').then(m => ({ default: m.NewsPage })))
export const FlashPage = lazy(() => import('./FlashPage').then(m => ({ default: m.FlashPage })))

function InfoTab({ symbol, name }: FrontendSlotContextMap['stock-preview.tabs']) {
  return (
    <div className="max-h-[60vh] overflow-y-auto">
      <StockInfoTab symbol={symbol} name={name} />
    </div>
  )
}

const extension: FrontendExtension = {
  id: 'official.info-center',
  apiVersion: 1,
  routes: [
    { id: 'info-announcements', path: '/info/announcements', component: AnnouncementsPage },
    { id: 'info-news', path: '/info/news', component: NewsPage },
    { id: 'info-flash', path: '/info/flash', component: FlashPage },
  ],
  navigation: [
    { id: 'info-announcements', routeId: 'info-announcements', label: '公告中心', icon: Megaphone, order: 80 },
    { id: 'info-news', routeId: 'info-news', label: '新闻中心', icon: Newspaper, order: 81 },
    { id: 'info-flash', routeId: 'info-flash', label: '快讯', icon: Radio, order: 82 },
  ],
  slots: [
    {
      name: 'stock-preview.tabs',
      id: 'info-center-stock-tab',
      label: '资讯',
      order: 95,
      component: InfoTab,
    },
  ],
}

export default extension
