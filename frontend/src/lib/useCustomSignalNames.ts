import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

/**
 * 信号定义 → 输出列名 → 用户命名 的映射。
 *
 * 复用 QK.customSignals 查询 (与 SignalPicker/信号库共享缓存, 不额外发请求)。
 * 告警/监控等展示侧应把返回值传给 cnSignal 第二参数 — 不传时 csg_/csgi_/
 * 迁移的 signal_* 会显示原始列名而非用户命名。
 *
 * 这是**全局展示映射**: AlertToast/Layout 在每页都会挂它, 而 /api/custom-signals
 * 只对策略/回测页放行。无权限账号会 403, 这里静默 + 不回退默认值 —— 缺映射时
 * cnSignal 会自动回退内置中文名/原始列名, 不影响任何功能, 也不该每页弹错误。
 */
export function useCustomSignalNames(): Record<string, string> {
  const query = useQuery({
    queryKey: QK.customSignals,
    queryFn: () => api.customSignalsList({ quiet: true }),
    retry: false,
  })
  return useMemo(() => {
    const names: Record<string, string> = {}
    for (const s of query.data?.signals ?? []) {
      if (s.id && s.name) {
        // 显式 column (迁移自内置的 signal_*) 优先于默认 csg_ 前缀
        names[s.column ?? `csg_${s.id}`] = s.name
        if (s.timeframe === 'intraday') names[`csgi_${s.id}`] = s.name
      }
    }
    return names
  }, [query.data])
}
