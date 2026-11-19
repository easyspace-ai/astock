// 按需注册的守门测试: 每个图表类型真的能渲染出来。
// 之前用整包 echarts 时不存在「忘了注册某个系列」的问题; 改成 echarts/core 之后,
// 漏注册的系列不会报错、只会静默不画 —— 这个测试专门挡这种情况。
//
// 用 SSR + SVG 渲染器跑, 不依赖 canvas / DOM 布局:
//   - 缺图表类型 → console.error 出现 "Series xxx is not exists"
//   - 缺组件 → option 里的 dataZoom/markLine 等不生效, 或直接报错
import { afterEach, describe, expect, it } from 'vitest'
import { SVGRenderer } from 'echarts/renderers'
import { echarts } from './echarts'

(echarts.use as (ext: unknown[]) => void)([SVGRenderer])

const errors: string[] = []
const origError = console.error
const origWarn = console.warn

afterEach(() => {
  console.error = origError
  console.warn = origWarn
  errors.length = 0
})

function render(option: Record<string, unknown>): string {
  console.error = (...args: unknown[]) => { errors.push(args.map(String).join(' ')) }
  console.warn = (...args: unknown[]) => { errors.push(args.map(String).join(' ')) }
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 400, height: 300 }) as any
  chart.setOption(option)
  const svg = chart.renderToSVGString() as string
  chart.dispose()
  return svg
}

describe('echarts 按需注册', () => {
  it('line / bar / candlestick 都能出图', () => {
    const svg = render({
      grid: {},
      tooltip: {},
      legend: {},
      dataZoom: [{ type: 'inside' }, { type: 'slider' }],
      xAxis: { type: 'category', data: ['a', 'b', 'c'] },
      yAxis: { type: 'value' },
      series: [
        { type: 'line', data: [1, 2, 3], markLine: { data: [{ yAxis: 2 }] }, markArea: { data: [[{ xAxis: 'a' }, { xAxis: 'b' }]] } },
        { type: 'bar', data: [3, 2, 1] },
        { type: 'candlestick', data: [[1, 2, 0.5, 1.5], [1.5, 2.5, 1, 2], [2, 3, 1.5, 2.5]] },
      ],
    })
    expect(errors.join('\n')).toBe('')
    expect(svg).toContain('<svg')
  })

  it('pie / heatmap / custom 都能出图', () => {
    const svg = render({
      grid: {},
      visualMap: { min: 0, max: 10, calculable: true, inRange: { color: ['#eee', '#333'] } },
      xAxis: { type: 'category', data: ['a', 'b'] },
      yAxis: { type: 'category', data: ['x', 'y'] },
      series: [
        { type: 'pie', data: [{ value: 1, name: 'A' }, { value: 2, name: 'B' }] },
        { type: 'heatmap', data: [[0, 0, 1], [1, 1, 5]] },
        {
          type: 'custom',
          renderItem: (_p: unknown, api: any) => ({
            type: 'group',
            children: [{ type: 'rect', shape: { x: api.coord([api.value(0), api.value(1)])[0], y: 10, width: 4, height: 4 } }],
          }),
          data: [[0, 1], [1, 2]],
        },
      ],
    })
    expect(errors.join('\n')).toBe('')
    expect(svg).toContain('<svg')
  })

  it('axisPointer / graphic 组件可用', () => {
    const svg = render({
      grid: {},
      tooltip: { trigger: 'axis' },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      graphic: [{ type: 'text', left: 10, top: 10, style: { text: 'ok' } }],
      xAxis: { type: 'category', data: ['a'] },
      yAxis: { type: 'value' },
      series: [{ type: 'line', data: [1] }],
    })
    expect(errors.join('\n')).toBe('')
    expect(svg).toContain('<svg')
  })
})
