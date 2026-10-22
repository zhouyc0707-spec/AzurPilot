/**
 * @fileoverview 系统公告拉取、未读状态管理与轮询订阅。
 */

import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { api } from '../api/client'
import type { Announcement } from '../api/types'
import { useConnection } from './context'

const READ_KEY = 'azurpilot.announcement.last-read-id'

interface AnnouncementStoreState {
  data: Announcement | null
  loading: boolean
  error: string
  lastReadId: string | null
}

let storeState: AnnouncementStoreState = {
  data: null,
  loading: false,
  error: '',
  lastReadId: readStoredId(),
}

const listeners = new Set<() => void>()
let inFlight: Promise<void> | undefined
let pollingConsumers = 0
let pollingTimer: ReturnType<typeof setInterval> | undefined

function notifyListeners() {
  listeners.forEach(fn => fn())
}

function readStoredId(): string | null {
  try {
    return localStorage.getItem(READ_KEY)
  } catch {
    return null
  }
}

function writeStoredId(id: string) {
  try {
    localStorage.setItem(READ_KEY, id)
  } catch {
    /* 存储不可用时会话内生效 */
  }
}

export function subscribeAnnouncement(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getAnnouncementSnapshot(): AnnouncementStoreState {
  return storeState
}

export function fetchAnnouncement(force = false): Promise<void> {
  // 外壳、主页与公告页共享同一请求；手动刷新也复用正在进行的查询。
  if (inFlight) return inFlight
  storeState = { ...storeState, loading: true }
  inFlight = Promise.resolve().then(() => api.request('announcement.get', { force })).then(result => {
    storeState = {
      ...storeState,
      data: result,
      loading: false,
      error: '',
      lastReadId: readStoredId(),
    }
  }, err => {
    storeState = {
      ...storeState,
      loading: false,
      error: (err as Error).message || '获取公告失败',
    }
  }).finally(() => {
    inFlight = undefined
    notifyListeners()
  })
  notifyListeners()
  return inFlight
}

/** 只由首个已连接的使用者启动轮询，最后一个退出时清理。 */
function retainPolling() {
  if (pollingConsumers++ === 0) {
    void fetchAnnouncement(false)
    pollingTimer = setInterval(() => { void fetchAnnouncement(false) }, 120_000)
  }
  return () => {
    if (--pollingConsumers === 0) {
      clearInterval(pollingTimer)
      pollingTimer = undefined
    }
  }
}

export function markAnnouncementAsRead() {
  const currentId = storeState.data?.announcementId
  if (currentId) {
    writeStoredId(currentId)
    storeState = {
      ...storeState,
      lastReadId: currentId,
    }
    notifyListeners()
  }
}

export function useAnnouncement() {
  const connection = useConnection()
  const state = useSyncExternalStore(subscribeAnnouncement, getAnnouncementSnapshot, getAnnouncementSnapshot)

  useEffect(() => {
    if (connection !== 'ready') return
    return retainPolling()
  }, [connection])

  const refresh = useCallback(async (force = true) => {
    if (connection === 'ready') {
      await fetchAnnouncement(force)
    }
  }, [connection])

  const unread = Boolean(
    state.data?.announcementId && state.data.announcementId !== state.lastReadId
  )

  return {
    data: state.data,
    loading: state.loading,
    error: state.error,
    unread,
    refresh,
    markAsRead: markAnnouncementAsRead,
  }
}
