"""旧统计格式兼容宿主服务；仅用于迁移已有 Broker 密文。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from module.statistics.opsi_broker import HostBroker
from module.statistics.opsi_keys import WindowsProvider, MacOSProvider, linux_provider, LinuxTPMProvider, ProviderUnavailable


def make_service(config):
    providers = {'windows': WindowsProvider, 'macos': MacOSProvider, 'linux': linux_provider,
                 'linux-tpm2': LinuxTPMProvider}
    if config.get('provider') not in providers:
        raise ProviderUnavailable('宿主平台配置无效')
    grants = config.get('grants')
    if not isinstance(grants, dict) or not grants or len(set(grants.values())) != len(grants) or any(
            not re.fullmatch('[0-9a-f]{64}', key) or not isinstance(value, str) or
            not re.fullmatch('[0-9a-f]{64}', value) for key, value in grants.items()):
        raise ProviderUnavailable('宿主授权配置无效')
    return HostBroker(providers[config['provider']](), grants).server(
        (config.get('bind', '127.0.0.1'), int(config.get('port', 25549))),
        config['certificate'], config['private_key'], config['ca'])


def main():
    parser = argparse.ArgumentParser(description='运行宿主统计服务')
    parser.add_argument('--config', required=True, type=Path, help='宿主服务配置路径')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    server = make_service(config)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
