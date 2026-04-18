<script setup lang="ts">
import { useData, useRoute } from 'vitepress'
import DefaultTheme from 'vitepress/theme'
import { nextTick, onUnmounted, provide, watch } from 'vue'

const { isDark } = useData()
const route = useRoute()

/** 首页 Hero：吉祥物与光晕在 XY 平面轻微错位平移（无 Z / 无 3D 旋转；仅宽屏 + 未开启减少动画） */
let heroTiltDetach: (() => void) | undefined

function setupHeroTiltParallax(): void {
  heroTiltDetach?.()
  heroTiltDetach = undefined
  if (typeof document === 'undefined' || typeof window === 'undefined') return
  if (prefersReducedMotion()) return

  const mqWide = window.matchMedia('(min-width: 960px)')

  const bind = (): (() => void) | undefined => {
    const container = document.querySelector(
      '.VPHero.has-image .image-container',
    ) as HTMLElement | null
    if (!container) return undefined

    /** 吉祥物最大位移（px，nx/ny ∈ [-1,1]） */
    const maxPxImg = 26
    /**
     * 背后渐变色块：与吉祥物反向平移 + 略大系数，肉眼可见「底色与角色错位」
     */
    const maxPxBg = 20

    const setVars = (nx: number, ny: number): void => {
      container.style.setProperty('--vp-hero-dx-img', `${(nx * maxPxImg).toFixed(1)}px`)
      container.style.setProperty('--vp-hero-dy-img', `${(ny * maxPxImg).toFixed(1)}px`)
      container.style.setProperty('--vp-hero-dx-bg', `${(-nx * maxPxBg).toFixed(1)}px`)
      container.style.setProperty('--vp-hero-dy-bg', `${(-ny * maxPxBg).toFixed(1)}px`)
    }

    const reset = (): void => {
      setVars(0, 0)
    }

    /** 以整个视口为参照：指针在屏幕任意位置移动都会带动 Hero 倾斜 */
    const onMove = (e: PointerEvent): void => {
      if (!mqWide.matches) {
        reset()
        return
      }
      const w = window.innerWidth || 1
      const h = window.innerHeight || 1
      const nx = (e.clientX / w) * 2 - 1
      const ny = (e.clientY / h) * 2 - 1
      setVars(
        Math.max(-1, Math.min(1, nx)),
        Math.max(-1, Math.min(1, ny)),
      )
    }

    const onBlur = (): void => {
      reset()
    }

    const onVisibility = (): void => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') reset()
    }

    window.addEventListener('pointermove', onMove, { passive: true })
    window.addEventListener('blur', onBlur)
    document.addEventListener('visibilitychange', onVisibility)

    const onMq = (): void => {
      if (!mqWide.matches) reset()
    }
    mqWide.addEventListener('change', onMq)

    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('blur', onBlur)
      document.removeEventListener('visibilitychange', onVisibility)
      mqWide.removeEventListener('change', onMq)
      container.style.removeProperty('--vp-hero-dx-bg')
      container.style.removeProperty('--vp-hero-dy-bg')
      container.style.removeProperty('--vp-hero-dx-img')
      container.style.removeProperty('--vp-hero-dy-img')
    }
  }

  const detach = bind()
  if (detach) heroTiltDetach = detach
}

watch(
  () => route.path,
  async () => {
    await nextTick()
    if (typeof window === 'undefined' || typeof requestAnimationFrame !== 'function') return
    requestAnimationFrame(() => {
      setupHeroTiltParallax()
    })
  },
  { immediate: true },
)

onUnmounted(() => {
  heroTiltDetach?.()
})

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

function enableViewTransition(): boolean {
  return (
    typeof document !== 'undefined' &&
    'startViewTransition' in document &&
    typeof (document as Document & { startViewTransition?: unknown })
      .startViewTransition === 'function' &&
    typeof window !== 'undefined' &&
    !prefersReducedMotion()
  )
}

provide('toggle-appearance', async ({ clientX: x, clientY: y }: MouseEvent) => {
  if (!enableViewTransition()) {
    isDark.value = !isDark.value
    return
  }

  const doc = document as Document & {
    startViewTransition: (cb: () => void | Promise<void>) => {
      ready: Promise<void>
    }
  }

  const radius = Math.hypot(
    Math.max(x, window.innerWidth - x),
    Math.max(y, window.innerHeight - y),
  )
  const clipPath = [
    `circle(0px at ${x}px ${y}px)`,
    `circle(${radius}px at ${x}px ${y}px)`,
  ]

  await doc
    .startViewTransition(async () => {
      isDark.value = !isDark.value
      await nextTick()
    })
    .ready

  document.documentElement.animate(
    {
      clipPath: isDark.value ? [...clipPath].reverse() : clipPath,
    },
    {
      duration: 360,
      easing: 'cubic-bezier(0.33, 1, 0.68, 1)',
      fill: 'forwards',
      pseudoElement: `::view-transition-${isDark.value ? 'old' : 'new'}(root)`,
    },
  )
})
</script>

<template>
  <DefaultTheme.Layout />
</template>

<style>
::view-transition-old(root),
::view-transition-new(root) {
  animation: none;
  mix-blend-mode: normal;
}

::view-transition-old(root),
.dark::view-transition-new(root) {
  z-index: 1;
}

::view-transition-new(root),
.dark::view-transition-old(root) {
  z-index: 9999;
}
</style>
