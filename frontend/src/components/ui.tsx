import * as LegacyUi from './LegacyUi'
import {getThemePreference, subscribeTheme, usesLegacyLayout} from '../app/theme'
import {useSyncExternalStore} from 'react'
/**
 * @fileoverview 通用 UI 基础原子组件（按钮、弹窗、加载状态、错误框等）。
 */

import type { ReactNode } from 'react'
import { AlertCircle, LoaderCircle } from 'lucide-react'
import type { Status } from '../api/types'
import { useApp } from '../app/context'
import { useDevOverride } from '../app/devOverride'
import { Alert, AlertDescription } from './ui/alert'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'
import { Empty as EmptyState, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from './ui/empty'

function useLegacyUi() {
  return usesLegacyLayout(useSyncExternalStore(subscribeTheme, getThemePreference, getThemePreference).theme)
}

const STATUS_VARIANTS = {
  running: 'default',
  stopped: 'secondary',
  error: 'destructive',
  updating: 'outline',
} as const

/** 实例状态徽章：按 Status 映射颜色与 i18n 文案。 */
function ModernStatusBadge({status, simulate = false}: {status: Status; simulate?: boolean}) {
  const {ui} = useApp()
  // 实例列表上的徽章跟随开发者工具的「模拟状态」；控件预览里的徽章不跟随。
  const override = useDevOverride().status
  const shown = simulate && override ? override : status
  return <Badge variant={STATUS_VARIANTS[shown]}>{{running: ui('status.running'), stopped: ui('status.stopped'), error: ui('status.error'), updating: ui('status.updating')}[shown]}</Badge>
}

/** 空态占位：图标 + 标题 + 可选描述，统一排版。 */
function ModernEmpty({icon, title, children}: {icon?: ReactNode; title: string; children?: ReactNode}) {
  return <EmptyState>
    <EmptyHeader>
      {icon && <EmptyMedia variant="icon">{icon}</EmptyMedia>}
      <EmptyTitle>{title}</EmptyTitle>
      {children && <EmptyDescription>{children}</EmptyDescription>}
    </EmptyHeader>
  </EmptyState>
}

/** 区域级加载占位：旋转图标 + 「加载中」文案，带 role=status 供读屏识别。 */
function ModernLoading(_props: Record<string, never>) {
  const {ui} = useApp()
  return <div data-slot="loading" role="status" className="flex min-h-[230px] items-center justify-center gap-2.5 text-muted-foreground">
    <LoaderCircle className="animate-spin" size={22}/>{ui('common.loading')}
  </div>
}

/** 错误提示框：destructive Alert，可带重试按钮。 */
function ModernErrorBox({message, retry}: {message: string; retry?: () => void}) {
  const {ui} = useApp()
  return <Alert variant="destructive">
    <AlertCircle/>
    <AlertDescription>
      <span>{message}</span>
      {retry && <Button variant="outline" size="sm" className="mt-2" onClick={retry}>{ui('common.retry')}</Button>}
    </AlertDescription>
  </Alert>
}

/** 简易弹窗：挂载即打开，点遮罩或 Esc 触发 onClose，关闭由调用方卸载完成。 */
function ModernModal({title, children, onClose, className = ''}: {title: string; children: ReactNode; onClose: () => void; className?: string}) {
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}>
    <DialogContent className={className}>
      <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
      {children}
    </DialogContent>
  </Dialog>
}

/** 页面动作组：控件外面包一层容器，挂载位置由调用方决定。 */
function ModernActionGroup({children}: {children: ReactNode}) {
  return <div className="flex flex-wrap items-center gap-2.5">{children}</div>
}

/** 页头：左侧标题、右侧动作区。 */
function ModernPageTitle({title, actions, className = ''}: {title: string; actions?: ReactNode; className?: string}) {
  return <div className={`flex items-center justify-between gap-4.5 ${className}`.trim()}>
    <h1 aria-label={title} className="text-3xl font-bold tracking-tight">{title}</h1>
    {actions && <ActionGroup>{actions}</ActionGroup>}
  </div>
}

export function StatusBadge(props: Parameters<typeof ModernStatusBadge>[0]) {
  return useLegacyUi() ? <LegacyUi.StatusBadge {...props}/> : <ModernStatusBadge {...props}/>
}

export function Empty(props: Parameters<typeof ModernEmpty>[0]) {
  return useLegacyUi() ? <LegacyUi.Empty {...props}/> : <ModernEmpty {...props}/>
}

export function Loading(props: Record<string, never> = {}) {
  return useLegacyUi() ? <LegacyUi.Loading/> : <ModernLoading {...props}/>
}

export function ErrorBox(props: Parameters<typeof ModernErrorBox>[0]) {
  return useLegacyUi() ? <LegacyUi.ErrorBox {...props}/> : <ModernErrorBox {...props}/>
}

export function Modal(props: Parameters<typeof ModernModal>[0]) {
  return useLegacyUi() ? <LegacyUi.Modal {...props}/> : <ModernModal {...props}/>
}

export function ActionGroup(props: Parameters<typeof ModernActionGroup>[0]) {
  return useLegacyUi() ? <LegacyUi.ActionGroup {...props}/> : <ModernActionGroup {...props}/>
}

export function PageTitle(props: Parameters<typeof ModernPageTitle>[0]) {
  return useLegacyUi() ? <LegacyUi.PageTitle {...props}/> : <ModernPageTitle {...props}/>
}
