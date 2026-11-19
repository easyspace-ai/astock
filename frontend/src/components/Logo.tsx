// 原创 logo:实心六边形机舱 + 镂空的长阳线
//
// 概念:
//   - 六边形实心块:设备 / 核心舱,在 14–18px 的小尺寸下仍是一个完整、识别度高的形状
//   - 镂空的长阳线:一根实体饱满、上影短的 K 线 — 直接指向行情与「突破」的语义
//   - 用负空间而不是描边画 K 线,小尺寸不会糊成一团
//
// fill 用 currentColor,继承父级 color (顶栏走品牌色 BRAND),换品牌包时自动跟随。
import { useId } from 'react'

interface LogoProps {
  className?: string
  size?: number
  style?: React.CSSProperties
}

export function Logo({ className, size = 32, style }: LogoProps) {
  const maskId = `logo-mask-${useId().replace(/:/g, '')}`
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      fill="none"
      className={className}
      style={style}
      role="img"
      aria-label="财富引擎"
    >
      <defs>
        {/* mask:白=保留(六边形),黑=镂空(K 线 body + 上下影线) */}
        <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="32" height="32">
          <path d="M16 4 L26.5 10 V22 L16 28 L5.5 22 V10 Z" fill="#fff" />
          <rect x="13.8" y="14" width="4.4" height="9.4" rx="0.6" fill="#000" />
          <line x1="16" y1="10.6" x2="16" y2="24.4" stroke="#000" strokeWidth="1" strokeLinecap="round" />
        </mask>
      </defs>
      <path d="M16 4 L26.5 10 V22 L16 28 L5.5 22 V10 Z" fill="currentColor" mask={`url(#${maskId})`} />
    </svg>
  )
}
