import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Download, Eye, EyeOff, ExternalLink, GripVertical, Folder, FolderMinus, LockKeyhole, Plus, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type Preferences } from '@/lib/api'
import { toast } from '@/components/Toast'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { usePermissions } from '@/lib/useAccount'
import { getNavigationEntries, menuNodesToLayout, type NavigationEntry } from '@/lib/navigation'
import { canonicalNavId, dissolveNavGroup, moveNavAfterHome, moveNavPage, navPageIds, type NavGroup, type NavLayout, type NavNode } from '@/lib/navLayout'
import { PERM } from '@/lib/permissions'
import { cn } from '@/lib/cn'

function SortableMenuRow({ node, entry, parentId, groups, hidden, busy, canEdit, focusNew, onMove, onHide, onRename, onDissolve, onRemove }: {
  node: NavNode; entry?: NavigationEntry; parentId: string | null; groups: NavGroup[]; hidden: boolean; busy: boolean; canEdit: boolean
  focusNew: boolean; onMove: (id: string, parent: string | null) => void; onHide: (id: string) => void
  onRename: (id: string, label: string) => void; onDissolve: (id: string) => void; onRemove: (entry: NavigationEntry) => void
}) {
  const home = node.id === '/'
  const { attributes, listeners, setNodeRef, transform, transition, isDragging, isOver } = useSortable({
    id: node.id, disabled: home || busy || !canEdit, data: { parentId, type: node.type },
  })
  const label = node.type === 'group' ? node.label : entry?.label ?? node.id
  const Icon = node.type === 'group' ? Folder : entry?.icon ?? ExternalLink
  return <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
    className={cn('relative flex flex-wrap items-center gap-2 border-b border-border/60 px-3 py-3 last:border-b-0 sm:px-4',
      parentId && 'pl-9 sm:pl-10', isDragging && 'z-10 bg-elevated shadow-lg', isOver && 'bg-accent/10', hidden && 'opacity-60')}
    data-nav-id={node.id}>
    <button type="button" {...attributes} {...listeners} disabled={home || busy || !canEdit} aria-label={`拖动${label}`}
      className="touch-none rounded p-1 text-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-30">
      {home ? <LockKeyhole className="h-4 w-4" /> : <GripVertical className="h-4 w-4" />}
    </button>
    <Icon className="h-4 w-4 shrink-0 text-muted" />
    <div className="min-w-0 flex-1 basis-28">
      {node.type === 'group' ? <input aria-label={`分类名称：${label}`} maxLength={32} value={node.label} readOnly={!canEdit} disabled={busy} autoFocus={focusNew}
        onChange={e => onRename(node.id, e.target.value)} className="h-8 w-full rounded border border-transparent bg-transparent px-0 text-sm font-medium text-foreground hover:border-border focus:border-accent focus:outline-none disabled:opacity-70" />
        : <div className="truncate text-sm font-medium text-foreground">{label}{home && <span className="ml-2 text-xs font-normal text-muted">固定置顶</span>}</div>}
      <div className="truncate text-[11px] text-muted">
        {node.type === 'group' ? `${node.children.length} 个菜单 · 拖到分类名称上可移入` : !entry ? '页面暂不可用，归属已保留'
          : !entry.visible ? '页面已停用，启用后在原分类恢复' : entry.type === 'builtin' ? '内置页面' : entry.type === 'analysis' ? '自定义分析页面' : '扩展页面'}
      </div>
    </div>
    {node.type === 'page' && !home && <select aria-label={`移动${label}到`} value={parentId ?? ''} disabled={busy || !canEdit}
      onChange={e => onMove(node.id, e.target.value || null)}
      className="h-8 w-32 max-w-full rounded-lg border border-border bg-base px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-50">
      <option value="">顶层菜单</option>{groups.map(group => <option key={group.id} value={group.id}>{group.label || '未命名分类'}</option>)}
    </select>}
    <div className="flex items-center gap-1">
      {node.type === 'group' ? <button type="button" disabled={busy || !canEdit} onClick={() => onDissolve(node.id)} aria-label={`解散${label}`}
        title="解散分类，子菜单移回顶层" className="rounded p-2 text-muted hover:bg-elevated hover:text-foreground disabled:opacity-40"><FolderMinus className="h-4 w-4" /></button>
        : <>
          <button type="button" disabled={home || busy} onClick={() => onHide(node.id)} aria-label={`${hidden ? '显示' : '隐藏'}${label}`}
            className="rounded p-2 text-muted hover:bg-elevated hover:text-accent disabled:opacity-30">{hidden ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button>
          {entry && <Link to={entry.type === 'analysis' ? '/admin?tab=pages&section=ext-pages' : entry.to}
            aria-label={`打开${label}`} className="rounded p-2 text-muted hover:bg-elevated hover:text-accent"><ExternalLink className="h-4 w-4" /></Link>}
          {canEdit && entry?.extensionId && <button type="button" disabled={busy} onClick={() => onRemove(entry)} aria-label={`删除源码扩展页${label}`}
            className="rounded p-2 text-muted hover:bg-danger/10 hover:text-danger disabled:opacity-30"><Trash2 className="h-4 w-4" /></button>}
        </>}
    </div>
  </div>
}

function RootDropTarget({ disabled }: { disabled: boolean }) {
  const { setNodeRef, isOver } = useDroppable({ id: 'nav:root', disabled })
  return <div ref={setNodeRef} className={cn('p-4 text-center text-xs text-muted', isOver && 'bg-accent/10 text-accent')}>拖到这里移回顶层，也可使用每行的归属选择框</div>
}

/** 前台页面管理只显示有页面（或用户新建的空分类）的分组，不把后台权限节点画成空菜单。 */
function frontLayout(layout: NavLayout): NavLayout {
  return {
    v: 1,
    items: layout.items.filter(node => node.type === 'page'
      || node.children.length > 0
      || node.id.startsWith('menu.custom.')),
  }
}

/** 看板下方间隙落点：拖到这里即排在看板之后（第二位）。 */
function HomeAfterDropTarget({ disabled }: { disabled: boolean }) {
  const { setNodeRef, isOver } = useDroppable({ id: 'nav:home-after', disabled })
  return <div ref={setNodeRef} data-drop-id="nav:home-after" aria-hidden
    className={cn('mx-3 h-2 transition-colors sm:mx-4', isOver && 'rounded bg-accent/25')} />
}

export function SettingsMenuSettingsPanel() {
  const qc = useQueryClient()
  const { can } = usePermissions()
  const canEdit = can(PERM.pagesManage)
  const { data: prefs } = usePreferences()
  const tree = useQuery({ queryKey: QK.navigationTree, queryFn: api.navigationTree })
  const menus = useQuery({ queryKey: QK.analysisMenus, queryFn: api.analysisMenus })
  const [removedNs, setRemovedNs] = useState<Set<string>>(new Set())
  const [draft, setDraft] = useState<NavLayout | null>(null)
  const [hiddenDraft, setHiddenDraft] = useState<string[] | null>(null)
  const [newGroupName, setNewGroupName] = useState('')
  const [newGroupId, setNewGroupId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  const entries = getNavigationEntries(menus.data?.items ?? [], tree.data?.menus ?? [])
    .filter(x => !x.extensionId || !removedNs.has(x.extensionId))
  const entryMap = new Map(entries.map(x => [x.id, x]))
  const pageMenuNodes = (tree.data?.menus ?? []).filter(node => node.group === 'page')
  const baseLayout = frontLayout(menuNodesToLayout(pageMenuNodes, entries, { includeEmptyGroups: true }))
  const backendNodes = (tree.data?.menus ?? []).filter(node => node.group === 'admin' || node.group === 'feature')
  const backendJson = JSON.stringify({ version: 1, nodes: backendNodes }, null, 2)
  const layout = draft ?? baseLayout
  const hidden = hiddenDraft ?? (prefs?.nav_hidden ?? []).map(canonicalNavId).filter(id => id !== '/')
  const dirty = draft !== null || hiddenDraft !== null
  const groups = layout.items.filter((x): x is NavGroup => x.type === 'group')
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))
  const busy = !prefs || tree.isLoading || menus.isLoading || tree.isError || menus.isError

  const save = useMutation({
    mutationFn: async () => {
      const layoutResult = canEdit ? await api.saveNavigationLayout(layout) : null
      const hiddenResult = await api.saveNavHidden(hidden)
      return { menus: layoutResult?.menus, nav_hidden: hiddenResult.nav_hidden }
    },
    onSuccess: result => {
      if (result.menus) qc.setQueryData(QK.navigationTree, { menus: result.menus })
      qc.setQueryData<Preferences>(QK.preferences, previous => previous ? { ...previous, nav_hidden: result.nav_hidden } : previous)
      qc.invalidateQueries({ queryKey: ['rbac', 'catalog'] })
      qc.invalidateQueries({ queryKey: QK.navigationTree })
      setDraft(null); setHiddenDraft(null); setSaved(true); setError('')
      toast(canEdit ? '菜单结构与隐藏设置已保存' : '隐藏设置已保存', 'success')
    },
    onError: (e: Error) => setError(e.message || '保存失败，修改已保留，请重试'),
  })

  const change = (value: NavLayout) => {
    if (!canEdit) return
    setDraft(value); setSaved(false); setError('')
  }
  const onMove = (id: string, parent: string | null) => change(moveNavPage(layout, id, parent))
  const onHide = (id: string) => {
    if (id === '/') return
    setHiddenDraft(hidden.includes(id) ? hidden.filter(x => x !== id) : [...hidden, id]); setSaved(false)
  }
  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!canEdit || !over || active.id === over.id || active.id === '/' || busy) return
    const id = String(active.id)
    if (over.id === '/') {
      toast('看板固定在首位，其他菜单排在其后即可', 'error')
      return
    }
    if (over.id === 'nav:home-after') {
      change(moveNavAfterHome(layout, id))
      return
    }
    const root = layout.items.find(x => x.id === id)
    if (root?.type === 'group') {
      const target = layout.items.find(x => x.id === over.id || navPageIds(x).includes(String(over.id)))
      const from = layout.items.findIndex(x => x.id === id)
      const to = over.id === 'nav:root' ? layout.items.length - 1 : layout.items.findIndex(x => x.id === target?.id)
      if (to > 0) change({ ...layout, items: arrayMove(layout.items, from, to) })
      return
    }
    if (over.id === 'nav:root') return onMove(id, null)
    const group = groups.find(x => x.id === over.id || x.children.some(page => page.id === over.id))
    const oldGroup = groups.find(x => x.children.some(page => page.id === id))
    if (group?.id === oldGroup?.id && over.id !== group?.id) {
      const siblings = group ? group.children : layout.items
      const from = siblings.findIndex(x => x.id === id)
      const to = siblings.findIndex(x => x.id === over.id)
      if (from >= 0 && to >= (group ? 0 : 1)) {
        if (group) change({ ...layout, items: layout.items.map(x => x.id === group.id
          ? { ...group, children: arrayMove(group.children, from, to) } : x) })
        else change({ ...layout, items: arrayMove(layout.items, from, to) })
      }
      return
    }
    change(moveNavPage(layout, id, group?.id ?? null, group?.id === over.id ? undefined : String(over.id)))
  }
  const addGroup = () => {
    if (!canEdit) return
    const label = newGroupName.trim()
    if (!label) return
    const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
    const group: NavGroup = { type: 'group', id: `group:${id}`, label, children: [] }
    change({ ...layout, items: [layout.items[0], group, ...layout.items.slice(1)] })
    setNewGroupId(group.id)
    setNewGroupName('')
  }

  const deleteExtension = useMutation({
    mutationFn: (ns: string) => api.pageAdminDeleteSourceExtension(ns),
    onSuccess: r => {
      setRemovedNs(previous => new Set(previous).add(r.deleted))
      toast(`已删除 ${r.deleted}（已备份），刷新后移除页面`, 'success')
    },
    onError: (e: Error) => toast(e.message || '删除失败', 'error'),
  })
  const remove = (entry: NavigationEntry) => {
    if (dirty) { setError('请先保存或撤销布局修改，再删除扩展页'); return }
    const ns = entry.extensionId
    if (!ns || !confirm(`删除源码扩展页「${entry.label}」？将先备份再物理删除 frontend/src/custom/${ns}/。`)) return
    if (confirm(`再次确认删除 ${ns}？页面入口将于刷新后移除。`)) deleteExtension.mutate(ns)
  }
  const row = (node: NavNode, parentId: string | null = null) => <SortableMenuRow key={node.id} node={node} entry={entryMap.get(node.id)}
    parentId={parentId} groups={groups} hidden={hidden.includes(node.id)} busy={busy || save.isPending || deleteExtension.isPending}
    canEdit={canEdit} focusNew={node.id === newGroupId}
    onMove={onMove} onHide={onHide}
    onRename={(id, label) => change({ ...layout, items: layout.items.map(x => x.type === 'group' && x.id === id ? { ...x, label } : x) })}
    onDissolve={id => change(dissolveNavGroup(layout, id))}
    onRemove={remove} />
  return <div className="max-w-5xl space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><h2 className="text-xl font-semibold text-foreground">前台页面</h2>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
          这里只管理前台侧栏页面：分组、排序和标签保存到后端统一菜单树；后台页面单独放在下方 JSON，不参与前台排序。
          {!canEdit && ' 当前账号没有页面管理权限，只能控制自己的显示/隐藏。'}
        </p></div>
      <Link to="/admin?tab=pages&section=ext-pages" className="rounded-lg border border-border px-3 py-2 text-xs text-secondary hover:bg-elevated">管理扩展页面</Link>
    </div>
    <p className="rounded-lg border border-border bg-elevated/30 px-3 py-2 text-xs leading-5 text-muted">
      新页面会先出现在权限目录，默认不授权；勾选权限后才会显示。排序与分组是全局设置，不是个人偏好。
    </p>
    {canEdit && <div className="flex flex-wrap items-center gap-2">
      <input aria-label="新分类名称" placeholder="分类名称，例如：研究分析" maxLength={32} value={newGroupName} disabled={busy}
        onChange={e => setNewGroupName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addGroup() }}
        className="h-9 min-w-0 flex-1 basis-48 rounded-lg border border-border bg-base px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-accent/30" />
      <button type="button" onClick={addGroup} disabled={busy || !newGroupName.trim()} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-xs text-foreground hover:bg-elevated disabled:opacity-40"><Plus className="h-4 w-4" />新建分类</button>
    </div>}
    {(tree.isError || menus.isError) && <p role="alert" className="text-sm text-danger">菜单加载失败，请刷新后重试。现有布局保持不变。</p>}
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <div className="rounded-xl border border-border bg-surface">
        <SortableContext items={layout.items.flatMap(node => [node.id, ...(node.type === 'group' ? node.children.map(x => x.id) : [])])} strategy={verticalListSortingStrategy}>
          {layout.items.map(node => <div key={node.id}>{row(node)}{node.id === '/' && <HomeAfterDropTarget disabled={busy || !canEdit} />}{node.type === 'group' && <div className="bg-elevated/20">
            {node.children.length ? node.children.map(page => row(page, node.id)) : <p className="px-12 py-3 text-xs text-muted">暂无子菜单，可拖入页面或通过归属选择框移入；保存后分类显示在侧栏。</p>}
          </div>}</div>)}
        </SortableContext>
        <RootDropTarget disabled={busy || !canEdit} />
      </div>
    </DndContext>
    {backendNodes.length > 0 && <details className="rounded-xl border border-border bg-surface">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-foreground">
        后台页面配置
        <span className="text-[11px] font-normal text-muted">JSON · 只读 · {backendNodes.length} 个节点</span>
        <button type="button" onClick={e => {
          e.preventDefault(); e.stopPropagation()
          const url = URL.createObjectURL(new Blob([backendJson], { type: 'application/json' }))
          const a = document.createElement('a')
          a.href = url; a.download = 'backend-menu.json'; a.click()
          URL.revokeObjectURL(url)
        }} className="ml-auto inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-secondary hover:bg-elevated"><Download className="h-3.5 w-3.5" />导出 JSON</button>
      </summary>
      <p className="border-t border-border px-4 py-2 text-[11px] leading-5 text-muted">后台页面与功能/管理权限同源，只用于权限矩阵；前台侧栏不显示这些节点。</p>
      <pre className="max-h-72 overflow-auto border-t border-border/60 bg-elevated/20 px-4 py-3 text-[11px] leading-5 text-secondary">{backendJson}</pre>
    </details>}
    {error && <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">{error}</p>}
    <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-base py-3">
      <span role="status" className="mr-auto text-xs text-muted">{busy ? '正在读取后端菜单树…' : save.isPending ? '正在保存…' : dirty ? '有未保存的修改' : saved ? '已保存' : '修改后点击保存，侧栏与权限树立即更新'}</span>
      <button type="button" disabled={busy || save.isPending || !dirty} onClick={() => { setDraft(null); setHiddenDraft(null); setError('') }}
        className="rounded-lg border border-border px-3 py-2 text-xs text-secondary hover:bg-elevated disabled:opacity-40">撤销修改</button>
      <button type="button" disabled={busy || save.isPending || !dirty} onClick={() => save.mutate()} className="rounded-lg bg-accent px-4 py-2 text-xs font-medium text-white hover:bg-accent/90 disabled:opacity-40">保存菜单设置</button>
    </div>
  </div>
}
