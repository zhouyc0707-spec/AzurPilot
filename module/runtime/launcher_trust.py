"""应用级启动器信任密钥和一次性登录票据。"""
import os
import secrets
import threading
import time
from module.base.runtime_params import TOKEN_TTL_SECONDS

TRUST_SECRET_ENV = 'AP_WEBUI_TRUST_SECRET'


def environment_secret():
    """新变量显式设置时优先，否则兼容已安装的旧启动器。"""
    return os.environ.get(TRUST_SECRET_ENV, os.environ.get('ALAS_WEBUI_TRUST_SECRET'))


class LauncherTrust:
    def __init__(self, secret=None):
        self.secret = str(secret or '').strip()
        self.lock = threading.Lock()
        self.tokens = {}

    def check_secret(self, candidate):
        return bool(self.secret and isinstance(candidate, str)
                    and secrets.compare_digest(candidate.encode(), self.secret.encode()))

    def issue(self):
        with self.lock:
            now = time.monotonic()
            self.tokens = {key: expiry for key, expiry in self.tokens.items() if expiry > now}
            if len(self.tokens) >= 128:
                self.tokens.pop(next(iter(self.tokens)))
            token = secrets.token_urlsafe(32)
            self.tokens[token] = now + TOKEN_TTL_SECONDS
            return {'ticket': token, 'ttl': TOKEN_TTL_SECONDS}

    def consume(self, token):
        with self.lock:
            return self.tokens.pop(token, 0) > time.monotonic()

    def clear(self):
        with self.lock:
            self.tokens.clear()


class LegacyLauncherTrust(LauncherTrust):
    """旧登录页面仍使用密码种子，但票据独立隔离、限量且只消费一次。"""

    webui_key = None

    def enabled(self):
        return bool(self.secret and self.webui_key and str(self.webui_key).strip())

    def issue_token(self):
        return self.issue()['ticket'] if self.enabled() else None


# 仅供 PyWebIO 的旧公共导入入口；React 应用使用自己的 LegacyLauncherTrust。
_legacy_trust = LegacyLauncherTrust()


def configure(secret, webui_key):
    _legacy_trust.clear()
    _legacy_trust.secret = str(secret or '').strip()
    _legacy_trust.webui_key = webui_key


def enabled():
    return _legacy_trust.enabled()


def check_secret(candidate):
    return _legacy_trust.check_secret(candidate)


def issue_token():
    return _legacy_trust.issue_token()


def validate_token(token):
    return _legacy_trust.consume(token)


def webui_key():
    return _legacy_trust.webui_key


def _reset():
    configure(None, None)
