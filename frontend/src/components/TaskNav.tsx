/**
 * @fileoverview 侧边栏任务分组与导航菜单入口组件。
 */

import { useSyncExternalStore } from 'react'
import { isDesktopDevice, TaskNavFlyout } from './TaskNavFlyout'
import { TaskNavTree } from './TaskNavTree'

function subscribeDesktop(callback: () => void) {
  if (typeof window === 'undefined') return () => {}
  window.addEventListener('resize', callback)
  const hoverMedia = typeof window.matchMedia === 'function' ? window.matchMedia('(hover: none)') : null
  hoverMedia?.addEventListener('change', callback)
  return () => {
    window.removeEventListener('resize', callback)
    hoverMedia?.removeEventListener('change', callback)
  }
}

/**
 * 响应式监听当前是否为桌面端（宽屏且支持鼠标悬停）。
 * SSR / 无 window 环境下默认视作桌面端以保证首屏和已有测试一致。
 */
export function useIsDesktop(): boolean {
  return useSyncExternalStore(
    subscribeDesktop,
    isDesktopDevice,
    () => true
  )
}

export type TaskNavProps = {
  defaultOpenKey?: string
  isDesktop?: boolean
  onNavigate?: () => void
}

/**
 * 侧栏任务菜单分流：
 * 电脑宽屏端所有主题使用向右浮出的二级菜单（TaskNavFlyout）。
 * 移动端/窄屏（宽度 <= 950px 或触屏设备）使用内嵌树状菜单（TaskNavTree），
 * 避免二级面板挤占抽屉之外的内容空间。
 */
export function TaskNav({ defaultOpenKey, isDesktop: isDesktopProp, onNavigate }: TaskNavProps = {}) {
  const responsiveDesktop = useIsDesktop()
  const isDesktop = isDesktopProp ?? responsiveDesktop

  return isDesktop
    ? <TaskNavFlyout defaultOpenKey={defaultOpenKey} onNavigate={onNavigate} />
    : <TaskNavTree defaultOpenKey={defaultOpenKey} onNavigate={onNavigate} />
}

