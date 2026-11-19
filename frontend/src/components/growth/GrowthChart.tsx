/**
 * 三年五倍验证 K 线 — 外部 OHLC 数据驱动，在谷底（蓝色向上箭头）与峰值（橙色圆点）
 * 位置打标记。基于 lightweight-charts v5 插件式 marker API，主题与通用蜡烛图一致。
 */
import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  CrosshairMode,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type SeriesMarker,
  type CandlestickData,
  type HistogramData,
} from 'lightweight-charts'
import { useChartTheme } from '@/lib/theme'
import type { OHLC } from '@/components/CandlestickChart'

const THEME = {
  background: 'transparent',
  bull: '#F04438',
  bear: '#12B76A',
  volBull: 'rgba(240,68,56,0.4)',
  volBear: 'rgba(18,183,106,0.4)',
  markerTrough: '#3B82F6',
  markerPeak: '#F59E0B',
}

export interface GrowthMarker {
  date: string
  price?: number | null
}

interface Props {
  data: OHLC[]
  height?: number
  trough?: GrowthMarker | null
  peak?: GrowthMarker | null
}

export function GrowthChart({ data, height = 420, trough, peak }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const markerRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const ct = useChartTheme()
  const ctRef = useRef(ct)
  ctRef.current = ct

  useEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        attributionLogo: true,
        background: { color: THEME.background },
        textColor: ctRef.current.text,
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: ctRef.current.grid },
        horzLines: { color: ctRef.current.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { labelVisible: false },
        horzLine: { labelVisible: false },
      },
      rightPriceScale: { borderColor: ctRef.current.border },
      timeScale: {
        borderColor: ctRef.current.border,
        timeVisible: false,
        secondsVisible: false,
      },
    })

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: THEME.bull,
      downColor: THEME.bear,
      borderUpColor: THEME.bull,
      borderDownColor: THEME.bear,
      wickUpColor: THEME.bull,
      wickDownColor: THEME.bear,
      lastValueVisible: false,
      priceLineVisible: false,
    })

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    })
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

    markerRef.current = createSeriesMarkers(candle, [])
    chartRef.current = chart
    candleRef.current = candle
    volRef.current = volume

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth })
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      markerRef.current?.detach()
      markerRef.current = null
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volRef.current = null
    }
  }, [height])

  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: { textColor: ct.text },
      grid: { vertLines: { color: ct.grid }, horzLines: { color: ct.grid } },
      rightPriceScale: { borderColor: ct.border },
      timeScale: { borderColor: ct.border },
    })
  }, [ct])

  useEffect(() => {
    if (!chartRef.current || !candleRef.current || !volRef.current || data.length === 0) return

    candleRef.current.setData(
      data.map(d => ({
        time: d.date as Time,
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      })) as CandlestickData[],
    )
    volRef.current.setData(
      data.map(d => ({
        time: d.date as Time,
        value: d.volume ?? 0,
        color: d.close >= d.open ? THEME.volBull : THEME.volBear,
      })) as HistogramData[],
    )

    const dates = new Set(data.map(d => d.date))
    const markers: SeriesMarker<Time>[] = []
    if (trough && dates.has(trough.date)) {
      markers.push({
        time: trough.date as Time,
        position: 'belowBar',
        color: THEME.markerTrough,
        shape: 'arrowUp',
        text: `谷底 ${trough.price?.toFixed(2) ?? ''}`,
      })
    }
    if (peak && dates.has(peak.date)) {
      markers.push({
        time: peak.date as Time,
        position: 'aboveBar',
        color: THEME.markerPeak,
        shape: 'circle',
        text: `峰值 ${peak.price?.toFixed(2) ?? ''}`,
      })
    }
    markerRef.current?.setMarkers(markers)

    const ts = chartRef.current.timeScale()
    if (data.length > 60) {
      const startIdx = data.length - 60
      ts.setVisibleRange({ from: data[startIdx].date as Time, to: data[data.length - 1].date as Time })
    } else {
      ts.fitContent()
    }
  }, [data, trough, peak])

  return <div ref={containerRef} className="w-full" style={{ height }} />
}
