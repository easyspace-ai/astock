// ECharts 按需注册入口 — 所有图表组件从这里取 `echarts`，不再整包引入。
//
// 之前每个图表文件都 `import * as echarts from 'echarts'`，会把整个 echarts（含
// 全部图表类型 + 全部组件 + 地图/GL 相关代码）打进一个 1MB 的单包。这里改为
// `echarts/core` + 显式 use()，只注册本项目真正用到的部分：
//   - 图表: line / bar / candlestick / pie / heatmap / custom
//   - 组件: grid / tooltip / axisPointer / legend / dataZoom / markLine / markArea /
//           visualMap / graphic
//   - 渲染器: canvas
// 新增图表类型时在下面补一行 use()，否则运行时拿不到该系列（会静默不渲染）。
import * as core from 'echarts/core'
import {
  BarChart,
  CandlestickChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  PieChart,
} from 'echarts/charts'
import {
  AxisPointerComponent,
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

core.use([
  LineChart,
  BarChart,
  CandlestickChart,
  PieChart,
  HeatmapChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  AxisPointerComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  MarkAreaComponent,
  VisualMapComponent,
  GraphicComponent,
  CanvasRenderer,
])

// 运行期只注册了上面 use() 的子集; 类型面沿用完整命名空间, 调用方无需改类型写法。
const echarts = core as unknown as typeof import('echarts')
export { echarts }
