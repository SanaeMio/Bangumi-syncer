import type { Theme } from 'vitepress'
import DefaultTheme from 'vitepress/theme'
import { nextTick } from 'vue'
import Layout from './Layout.vue'
import './custom.css'

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/** 文档路由切换：内容区淡入 + 轻微上移（与主题切换的 View Transition 分离） */
function runRouteContentAnimation(): void {
  if (typeof document === 'undefined') return
  if (prefersReducedMotion()) return
  requestAnimationFrame(() => {
    const el = document.querySelector('.VPContent')
    if (!(el instanceof HTMLElement)) return
    el.classList.remove('vp-route-enter')
    void el.offsetWidth
    el.classList.add('vp-route-enter')
    const done = (): void => {
      el.classList.remove('vp-route-enter')
    }
    /* 首页含多段错层动画，勿依赖首个 animationend（会过早移除类名） */
    window.setTimeout(done, 720)
  })
}

let skipFirstRouteAnim = true

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ router }) {
    const prev = router.onAfterRouteChange
    router.onAfterRouteChange = (href) => {
      void prev?.(href)
      if (typeof document === 'undefined') return
      if (skipFirstRouteAnim) {
        skipFirstRouteAnim = false
        return
      }
      void nextTick(() => runRouteContentAnimation())
    }
  },
} satisfies Theme
