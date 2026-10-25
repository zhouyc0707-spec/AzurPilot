"""组装启动器本地通道与本地保留的通知通道。

启动器控制、状态、报告与可信登录复用 :mod:`module.api.launcher_api` 的同一套
处理器和限流状态；通知端点复用 :mod:`module.api.notify_api` 的广播与队列，
保留旧 Python 导入入口，供通知模块投递、外部启动器订阅。应用工厂只注册这里的路由集合，并将其放在 SPA
静态资源兜底之前，避免启动器拿到 ``index.html`` 而非 JSON 或 SSE。
"""
from module.api import launcher_api, notify_api

# 保留既有 Python 导入入口；实际处理逻辑与上游启动器接口共用。
api_launcher_status = launcher_api.status
api_launcher_startup = launcher_api.startup
api_launcher_stream = launcher_api.stream
api_launcher_report = launcher_api.report
api_launcher_trusted_login = launcher_api.trusted_login
launcher_login = launcher_api.login_seed
REACT_PASSWORD_KEY = launcher_api.REACT_PASSWORD_KEY
LEGACY_PASSWORD_KEY = launcher_api.LEGACY_PASSWORD_KEY

# 旧模块的内部入口也指向共用状态，兼容本地调用而不形成第二套限流。
_LAUNCHER_SECRET_FAILURES = launcher_api._secret_failures
_LAUNCHER_SECRET_WINDOW = launcher_api.SECRET_FAILURE_WINDOW
_LAUNCHER_SECRET_MAX_FAILURES = launcher_api.SECRET_FAILURE_LIMIT


def _launcher_secret_try_allowed():
    return launcher_api._secret_try_allowed()


def _launcher_secret_failure():
    launcher_api._secret_failures.append(launcher_api.time.time())


def _launcher_login_denied():
    return launcher_api._login_denied()

api_notify = notify_api.notify
api_notify_stream = notify_api.notify_stream


def __getattr__(name):
    """旧通知队列的读取入口始终指向统一状态，不保留静态副本。"""
    if name == '_notification_queue':
        return notify_api._notification_queue
    raise AttributeError(f'{__name__!r} 没有属性 {name!r}')


def build_routes(service=None):
    """保持公共入口，应用工厂为每个实例绑定自己的启动器服务。"""
    return [*notify_api.routes(), *launcher_api.routes(service)]


routes = build_routes()
