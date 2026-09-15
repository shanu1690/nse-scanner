import { useEffect, useRef, useState } from 'react'
import {
  createChart, ColorType, LineStyle, CrosshairMode,
  CandlestickSeries, HistogramSeries, LineSeries,
} from 'lightweight-charts'

const TIMEFRAMES = [
  { id: '3m', label: '3M', bars: 63 },
  { id: '6m', label: '6M', bars: 126 },
  { id: '1y', label: '1Y', bars: 250 },
]

function themeColors() {
  const s = getComputedStyle(document.documentElement)
  const get = (v, fallback) => s.getPropertyValue(v).trim() || fallback
  return {
    bg: get('--panel', '#171e2e'),
    text: get('--muted', '#8b96ad'),
    border: get('--border', '#2a3550'),
    up: get('--up', '#22c55e'),
    down: get('--down', '#ef4444'),
    accent: get('--accent', '#5b8cff'),
  }
}

// series row: [date, open, high, low, close, volume, ema21, ema50, ema200, dcHigh20, dcLow20]
export default function CandleChart({ series, levels, height = 340 }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const [timeframe, setTimeframe] = useState('6m')

  useEffect(() => {
    if (!containerRef.current || !series || series.length < 2) return undefined
    const colors = themeColors()
    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: colors.bg }, textColor: colors.text },
      grid: { vertLines: { color: colors.border }, horzLines: { color: colors.border } },
      rightPriceScale: { borderColor: colors.border },
      timeScale: { borderColor: colors.border },
      crosshair: { mode: CrosshairMode.Normal },
      autoSize: true,
    })
    chartRef.current = chart

    const tf = TIMEFRAMES.find((t) => t.id === timeframe) || TIMEFRAMES[1]
    const rows = series.slice(-tf.bars)

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: colors.up, downColor: colors.down, borderVisible: false,
      wickUpColor: colors.up, wickDownColor: colors.down,
    })
    candleSeries.setData(rows.map((r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] })))

    const volSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' }, priceScaleId: 'vol',
      color: colors.accent,
    })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } })
    volSeries.setData(rows.map((r) => ({
      time: r[0], value: r[5] || 0,
      color: r[4] >= r[1] ? `${colors.up}66` : `${colors.down}66`,
    })))

    const emaLine = (idx, color) => {
      const data = rows.filter((r) => r[idx] !== null && r[idx] !== undefined)
        .map((r) => ({ time: r[0], value: r[idx] }))
      if (!data.length) return
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
      s.setData(data)
    }
    emaLine(6, colors.accent)       // EMA21
    emaLine(7, '#c084fc')           // EMA50
    emaLine(8, colors.text)         // EMA200

    if (levels) {
      const lines = [
        ['entry', colors.accent, 'Entry'],
        ['stop', colors.down, 'Stop'],
        ['target1', colors.up, 'T1'],
        ['target2', colors.up, 'T2'],
      ]
      for (const [key, color, label] of lines) {
        const price = levels[key]
        if (price === null || price === undefined) continue
        candleSeries.createPriceLine({
          price, color, lineWidth: 1, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: label,
        })
      }
    }

    chart.timeScale().fitContent()

    // autoSize: true above already installs the chart's own internal
    // ResizeObserver -- a second one here would just fight it.
    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [series, levels, timeframe, height])

  if (!series || series.length < 2) return <div className="loading">No price series.</div>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span className="legend">— close · — EMA21 · — EMA50 · — EMA200{levels ? ' · - - entry/stop/targets' : ''}</span>
        <div className="tf-switcher">
          {TIMEFRAMES.map((t) => (
            <button key={t.id} className={timeframe === t.id ? 'active' : ''} onClick={() => setTimeframe(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div ref={containerRef} style={{ width: '100%' }} />
    </div>
  )
}
