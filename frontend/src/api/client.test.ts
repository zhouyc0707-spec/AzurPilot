import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from './client'

class FakeSocket {
  static OPEN = 1
  static latest: FakeSocket
  readyState = 1
  onmessage?: (event: {data: string}) => void
  onclose?: () => void
  sent: Record<string, unknown>[] = []
  url: string
  constructor(url: string | URL) {FakeSocket.latest = this; this.url = String(url)}
  send(message: string) {this.sent.push(JSON.parse(message))}
  close() {if (this.readyState === 3) return; this.readyState = 3; this.onclose?.()}
  emit(message: unknown) {this.onmessage?.({data: JSON.stringify(message)})}
}

describe('WebSocket 客户端', () => {
  let client: ApiClient
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('window', {location: {href: 'http://localhost:5173/', protocol: 'http:'}})
    vi.stubGlobal('document', {baseURI: 'http://localhost:5173/'})
    vi.stubGlobal('WebSocket', FakeSocket)
    client = new ApiClient(); client.connect()
    FakeSocket.latest.emit({v: 1, type: 'event', topic: 'session', seq: 1, data: {authRequired: false}})
  })
  afterEach(() => {client.disconnect(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks()})
  it('乱序响应按请求 ID 关联', async () => {
    const first = client.request('system.ping', {})
    const second = client.request('instances.list', {})
    const socket = FakeSocket.latest
    socket.emit({v: 1, type: 'response', id: socket.sent[1].id, ok: true, result: []})
    socket.emit({v: 1, type: 'response', id: socket.sent[0].id, ok: true, result: {pong: true}})
    expect(await first).toEqual({pong: true}); expect(await second).toEqual([])
  })
  it('断线拒绝未完成请求，不自动重复写操作', async () => {
    const request = client.request('instances.create', {name: 'pilot'})
    const rejection = expect(request).rejects.toMatchObject({code: 'DISCONNECTED'})
    FakeSocket.latest.close(); await rejection
    await vi.advanceTimersByTimeAsync(2000)
    expect(FakeSocket.latest.sent).toEqual([])
  })
  it('请求超时后清理等待项', async () => {
    const request = client.request('system.ping', {})
    const rejection = expect(request).rejects.toMatchObject({code: 'TIMEOUT'})
    await vi.advanceTimersByTimeAsync(45000); await rejection
  })
  it('认证完成前不发送业务请求', async () => {
    client.disconnect(); client = new ApiClient(); client.connect()
    FakeSocket.latest.emit({v: 1, type: 'event', topic: 'session', seq: 1, data: {authRequired: true}})
    await expect(client.request('instances.list', {})).rejects.toMatchObject({code: 'DISCONNECTED'})
    expect(FakeSocket.latest.sent).toHaveLength(0)
  })
  it('WebSocket 地址跟随 document.baseURI，远程访问隧道前缀不丢', () => {
    // 地址与 peer_id 都是占位符，不要填真实隧道。
    vi.stubGlobal('window', {location: {href: 'https://tunnel.example.com/example-peer-id/', protocol: 'https:'}})
    vi.stubGlobal('document', {baseURI: 'https://tunnel.example.com/example-peer-id/'})
    client.disconnect(); client = new ApiClient(); client.connect()
    expect(FakeSocket.latest.url).toBe('wss://tunnel.example.com/example-peer-id/api/v1/ws')
  })
  it('保留校验失败的诊断详情供脚本编辑器定位行列', async () => {
    const request = client.request('system.ping', {})
    const rejection = expect(request).rejects.toMatchObject({
      code: 'INVALID_PARAMS', details: [{message: '不允许调用 os.execute', line: 4, column: 12}],
    })
    const sent = FakeSocket.latest.sent.at(-1)!
    FakeSocket.latest.emit({v: 1, type: 'response', id: sent.id, ok: false, error: {
      code: 'INVALID_PARAMS', message: '策略校验失败', details: [{message: '不允许调用 os.execute', line: 4, column: 12}],
    }})
    await rejection
  })
  it('刷新后用已保存的密码登录，密码失效时清除旧值', async () => {
    const values = new Map<string, string>()
    Object.assign(window, {localStorage: {getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)}})
    const login = client.login('测试访问密码')
    FakeSocket.latest.emit({v: 1, type: 'response', id: FakeSocket.latest.sent.at(-1)!.id, ok: true, result: {authenticated: true}})
    await login
    client.disconnect(); client = new ApiClient(); client.connect()
    FakeSocket.latest.emit({v: 1, type: 'event', topic: 'session', seq: 1, data: {authRequired: true}})
    expect(FakeSocket.latest.sent.at(-1)).toMatchObject({method: 'auth.login', params: {password: '测试访问密码'}})
    FakeSocket.latest.emit({v: 1, type: 'response', id: FakeSocket.latest.sent.at(-1)!.id, ok: false, error: {code: 'UNAUTHORIZED', message: '密码已更换'}})
    await vi.advanceTimersByTimeAsync(0)
    expect(client.getSnapshot()).toBe('auth')
    expect(values.size).toBe(0)
  })
  it('启动器票据只认证当前连接，立即清除地址中的票据且不保存访问密码', async () => {
    client.disconnect()
    const replaceState = vi.fn()
    const setItem = vi.fn()
    vi.stubGlobal('window', {
      location: {href: 'http://localhost:5173/?launcherTicket=opaque_ticket&view=settings', protocol: 'http:'},
      history: {replaceState}, localStorage: {getItem: () => null, setItem},
    })
    client = new ApiClient(); client.connect()
    expect(replaceState).toHaveBeenCalledWith(null, '', 'http://localhost:5173/?view=settings')
    FakeSocket.latest.emit({v: 1, type: 'event', topic: 'session', seq: 1, data: {authRequired: true}})
    const request = FakeSocket.latest.sent.at(-1)!
    expect(request).toMatchObject({method: 'auth.launcher', params: {ticket: 'opaque_ticket'}})
    FakeSocket.latest.emit({v: 1, type: 'response', id: request.id, ok: true, result: {authenticated: true}})
    await vi.advanceTimersByTimeAsync(0)
    expect(client.getSnapshot()).toBe('ready')
    expect(setItem).not.toHaveBeenCalled()
  })

  describe('主动重启后的重连', () => {
    beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })

    async function requestRestart() {
      const request = client.request('system.restart', {})
      FakeSocket.latest.emit({v: 1, type: 'response', id: FakeSocket.latest.sent.at(-1)!.id, ok: true, result: {restarting: true}})
      await request
    }

    async function expectReconnectAfter(delay: number) {
      const socket = FakeSocket.latest
      await vi.advanceTimersByTimeAsync(delay - 1)
      expect(FakeSocket.latest).toBe(socket)
      await vi.advanceTimersByTimeAsync(1)
      expect(FakeSocket.latest).not.toBe(socket)
    }

    it('主动重启窗口内连续快速重试，不重发重启请求', async () => {
      await requestRestart()
      for (let attempt = 0; attempt < 5; attempt++) {
        FakeSocket.latest.close()
        await expectReconnectAfter(600)
        expect(FakeSocket.latest.sent).toEqual([])
      }
    })

    it('旧服务在返回重启响应前断开，仍进入快速重连', async () => {
      const request = client.request('system.restart', {})
      const rejection = expect(request).rejects.toMatchObject({code: 'DISCONNECTED'})
      FakeSocket.latest.close()
      await rejection
      await expectReconnectAfter(600)
    })

    it('请求等待超时不代表重启被拒绝，保留剩余快速窗口', async () => {
      const request = client.request('system.restart', {})
      const rejection = expect(request).rejects.toMatchObject({code: 'TIMEOUT'})
      await vi.advanceTimersByTimeAsync(45000)
      await rejection
      FakeSocket.latest.close()
      await expectReconnectAfter(600)
    })

    it.each(['CONFLICT', 'UNAVAILABLE', 'UNAUTHORIZED'])('重启被明确拒绝（%s）后使用普通退避', async code => {
      const request = client.request('system.restart', {})
      const rejection = expect(request).rejects.toMatchObject({code})
      FakeSocket.latest.emit({v: 1, type: 'response', id: FakeSocket.latest.sent.at(-1)!.id, ok: false, error: {code, message: '重启已拒绝'}})
      await rejection
      FakeSocket.latest.close()
      await expectReconnectAfter(800)
      FakeSocket.latest.close()
      await expectReconnectAfter(1600)
    })

    it('普通故障保持原有指数退避', async () => {
      for (const delay of [800, 1600, 3200, 6400, 12800, 15000]) {
        FakeSocket.latest.close()
        await expectReconnectAfter(delay)
      }
    })

    it('快速窗口到期后回到普通退避，不无限快速重试', async () => {
      await requestRestart()
      for (let attempt = 0; attempt < 100; attempt++) {
        FakeSocket.latest.close()
        await expectReconnectAfter(600)
      }
      FakeSocket.latest.close()
      await expectReconnectAfter(15000)
    })

    it('新会话成功后清除快速窗口并重置普通退避', async () => {
      await requestRestart()
      FakeSocket.latest.close()
      await expectReconnectAfter(600)
      FakeSocket.latest.emit({v: 1, type: 'event', topic: 'session', data: {authRequired: false}})
      expect(client.getSnapshot()).toBe('ready')
      FakeSocket.latest.close()
      await expectReconnectAfter(800)
      FakeSocket.latest.close()
      await expectReconnectAfter(1600)
    })

    it('需要用户认证时退出快速窗口', async () => {
      await requestRestart()
      FakeSocket.latest.close()
      await expectReconnectAfter(600)
      FakeSocket.latest.emit({v: 1, type: 'event', topic: 'session', data: {authRequired: true}})
      expect(client.getSnapshot()).toBe('auth')
      FakeSocket.latest.close()
      await expectReconnectAfter(1600)
    })

    it('重连后已保存密码失效时退出快速窗口', async () => {
      const login = client.login('测试访问密码')
      FakeSocket.latest.emit({v: 1, type: 'response', id: FakeSocket.latest.sent.at(-1)!.id, ok: true, result: {authenticated: true}})
      await login
      await requestRestart()
      FakeSocket.latest.close()
      await expectReconnectAfter(600)
      FakeSocket.latest.emit({v: 1, type: 'event', topic: 'session', data: {authRequired: true}})
      const authRequest = FakeSocket.latest.sent.at(-1)!
      expect(authRequest.method).toBe('auth.login')
      FakeSocket.latest.emit({v: 1, type: 'response', id: authRequest.id, ok: false, error: {code: 'UNAUTHORIZED', message: '密码已更换'}})
      await vi.advanceTimersByTimeAsync(0)
      expect(client.getSnapshot()).toBe('auth')
      FakeSocket.latest.close()
      await expectReconnectAfter(1600)
    })

    it('显式断开取消快速重试，随后手动连接使用普通退避', async () => {
      await requestRestart()
      const socket = FakeSocket.latest
      socket.close()
      client.disconnect()
      await vi.advanceTimersByTimeAsync(1000)
      expect(FakeSocket.latest).toBe(socket)
      client.connect()
      FakeSocket.latest.close()
      await expectReconnectAfter(1600)
    })

    it('重启请求未能发送时不留下快速窗口', async () => {
      vi.spyOn(FakeSocket.latest, 'send').mockImplementationOnce(() => {throw new Error('发送失败')})
      await expect(client.request('system.restart', {})).rejects.toThrow('发送失败')
      FakeSocket.latest.close()
      await expectReconnectAfter(800)
    })
  })
})
