// @vitest-environment jsdom
// 守卫重定向的落地地址解析 — 防止登录页把 `?redirect=` 丢掉而弹回看板,
// 同时挡住 `//evil.com` / 绝对 URL 这类开放重定向写法。
import { beforeEach, describe, expect, it } from 'vitest'
import { redirectTarget } from './Auth'

function at(search: string) {
  window.history.replaceState({}, '', `/${search}`)
}

beforeEach(() => at(''))

describe('redirectTarget', () => {
  it('没有 redirect 参数时回看板', () => {
    at('')
    expect(redirectTarget()).toBe('/')
  })

  it('保留站内深链(含查询串)', () => {
    at('?redirect=%2Fstock-analysis%3Fsymbol%3D600519.SH')
    expect(redirectTarget()).toBe('/stock-analysis?symbol=600519.SH')
  })

  it('保留核心页深链', () => {
    at('?redirect=%2Fregime')
    expect(redirectTarget()).toBe('/regime')
  })

  it('拒绝协议相对与外部地址', () => {
    at('?redirect=%2F%2Fevil.example.com')
    expect(redirectTarget()).toBe('/')
    at('?redirect=https%3A%2F%2Fevil.example.com')
    expect(redirectTarget()).toBe('/')
  })
})
