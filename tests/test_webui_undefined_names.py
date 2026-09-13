"""静态检查：WebUI 视图模块里不得出现「用了但没定义/没导入」的名字。

这类错误在模块导入时不会被发现，只在对应渲染分支执行时才抛 NameError，
而渲染异常会被上层捕获成日志，表现为「某个板块凭空消失」。

只做保守检查：跳过带 ``import *`` 的文件（名字可能来自星号导入），
并忽略双下划线名字与类型注解里的名字。
"""

import ast
import builtins
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEBUI_DIR = PROJECT_ROOT / "module" / "webui"


def _collect_bound_names(tree):
    """收集模块中可见的绑定名（导入、定义、赋值、参数、推导式变量）。"""
    bound = set(dir(builtins))

    class Visitor(ast.NodeVisitor):
        def visit_Import(self, node):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])

        def visit_ImportFrom(self, node):
            for alias in node.names:
                bound.add(alias.asname or alias.name)

        def _bind_args(self, args):
            for arg in (
                list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)
            ):
                bound.add(arg.arg)
            if args.vararg:
                bound.add(args.vararg.arg)
            if args.kwarg:
                bound.add(args.kwarg.arg)

        def visit_FunctionDef(self, node):
            bound.add(node.name)
            self._bind_args(node.args)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            self._bind_args(node.args)
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            bound.add(node.name)
            self.generic_visit(node)

        def visit_Name(self, node):
            # 赋值目标与删除目标都算绑定（含推导式里的循环变量）
            if not isinstance(node.ctx, ast.Load):
                bound.add(node.id)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node):
            if node.name:
                bound.add(node.name)
            self.generic_visit(node)

        def visit_Global(self, node):
            bound.update(node.names)

        def visit_Nonlocal(self, node):
            bound.update(node.names)

    Visitor().visit(tree)
    return bound


def _undefined_names(path: Path):
    source = path.read_text(encoding="utf-8")
    if re.search(r"^\s*from\s+\S+\s+import\s+\*", source, flags=re.M):
        return []
    tree = ast.parse(source)
    bound = _collect_bound_names(tree)
    used = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return sorted(
        name
        for name in used - bound
        if not name.startswith("__") and not name.startswith("_")
    )


class TestWebuiNamesAreDefined(unittest.TestCase):
    def test_no_view_module_uses_an_undefined_name(self):
        problems = {}
        for path in sorted(WEBUI_DIR.glob("*.py")):
            missing = _undefined_names(path)
            if missing:
                problems[path.name] = missing
        self.assertEqual({}, problems, "存在未定义的名字，运行到该分支会抛 NameError")


if __name__ == "__main__":
    unittest.main()
