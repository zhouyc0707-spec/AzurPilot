"""测试用的 PyWebIO 输出打桩。

**为什么需要**：PyWebIO 的输出函数（`put_html` / `put_row` / `put_scope` /
`put_button` …）在没有活动会话时被调用会进入「脚本模式」—— PyWebIO 会自己起一个
服务器，并调用 `open_webbrowser_on_server_started` **用系统默认浏览器打开页面**：

    # pywebio/platform/tornado.py
    async def open_webbrowser_on_server_started(host, port):
        url = 'http://%s:%s' % (host, port)
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

于是跑单元测试时会凭空弹出「PyWebIO Application」标签页，进程还会一直挂着。

各测试 harness 原先只挑自己用到的那几个函数打桩，漏掉一个（例如渲染路径里新加了
`put_scope` / `put_button`）就会重新开始弹窗 —— 而且很难从测试结果看出来。
这里提供一次性的整体打桩，渲染测试统一用它，新增输出调用不会再漏。

**第二道防线**：即便某个 harness 漏打了桩，也不该真的弹浏览器。PyWebIO 的
「脚本模式」在 Tornado 平台上是这样开浏览器的（`pywebio/platform/tornado.py`）：

    if "PYWEBIO_SCRIPT_MODE_PORT" not in os.environ:
        tornado.ioloop.IOLoop.current().spawn_callback(
            open_webbrowser_on_server_started, '127.0.0.1', port)

注意它**不受 `auto_open_webbrowser` 控制**，只认这个环境变量。所以在导入本模块时
就把它设上：漏桩的 harness 顶多让脚本模式起个后台服务器，不会再往用户桌面上丢
标签页。
"""

import os
from contextlib import ExitStack, contextmanager
from importlib import import_module
from unittest.mock import MagicMock, patch

# 必须早于任何会进入脚本模式的调用；模块顶层执行，import 本模块即生效。
os.environ.setdefault("PYWEBIO_SCRIPT_MODE_PORT", "0")


class _StubOutput:
    """桩返回值：PyWebIO 的输出对象支持链式 ``.style(...)`` 等调用。

    ``put_row(...).style("...")`` 这类写法很常见，若桩返回 ``None`` 会在
    ``.style`` 上抛 AttributeError —— 那是桩的问题，不是被测代码的问题。
    """

    def __getattr__(self, name):
        return MagicMock(name=name)


# 渲染路径可能用到的全部输出函数
OUTPUT_NAMES = (
    "put_text",
    "put_html",
    "put_row",
    "put_column",
    "put_scope",
    "put_button",
    "put_buttons",
    "put_markdown",
    "put_table",
    "put_image",
    "put_widget",
    "put_loading",
    "put_processbar",
)


@contextmanager
def stub_pywebio_output(*modules, capture=None):
    """在给定模块里把所有 PyWebIO 输出函数替换成空实现。

    Args:
        *modules: 需要打桩的模块字符串（如
            ``"module.webui.app_stat_opsi"``）。只有这些模块里暴露出来的名字会被
            替换 —— 这也是测试断言会检查的调用点。
        capture: 可选的 ``dict``；按函数名记录**第一次**调用（与原测试里
            ``setdefault`` 的语义一致，因此同一函数被多次调用时，最先输出的那个
            会留在记录里 —— 例如面板 HTML 之后的 ``<style>`` 注入不会顶掉面板）。
            形如 ``{"put_html": ("<div…>",)}``。

    Yields:
        dict: 调用记录（传了 ``capture`` 时就是它本身）。
    """
    records = capture if capture is not None else {}
    with ExitStack() as stack:
        for module_name in modules:
            module = import_module(module_name)
            for name in OUTPUT_NAMES:
                # 只打桩该模块里真实存在的名字：不存在的说明它不从这个模块导出，
                # 用 create=True 造假属性反而可能掩盖真实的导入错误
                if not hasattr(module, name):
                    continue

                def stub(*args, _name=name, **kwargs):
                    records.setdefault(_name, args)
                    return _StubOutput()

                stack.enter_context(
                    patch(f"{module_name}.{name}", side_effect=stub)
                )
        yield records
