"""上游静态工具列表引用的最小安全计算器兼容实现."""

import ast
import operator
from collections.abc import Callable

from langchain_core.tools import tool

_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _evaluate(node: ast.AST) -> float:
    """只解释数值常量和白名单算术节点."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and (abs(left) > 1_000_000 or abs(right) > 100):
            raise ValueError("Exponentiation is outside the supported range")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    raise ValueError("Only basic arithmetic expressions are supported")


@tool
def calculator(expression: str) -> str:
    """计算只包含数字、括号及基本算术运算符的表达式."""
    if len(expression) > 200:
        raise ValueError("Expression is too long")
    tree = ast.parse(expression, mode="eval")
    result = _evaluate(tree)
    if not (-1e100 < result < 1e100):
        raise ValueError("Result is outside the supported range")
    return str(int(result)) if result.is_integer() else str(result)
