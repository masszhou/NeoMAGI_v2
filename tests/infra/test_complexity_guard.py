import ast

from infra.complexity_guard import _body_metrics


def _function_metrics(source: str) -> tuple[int, int]:
    module = ast.parse(source)
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    return _body_metrics(function.body)


def test_elif_chain_counts_as_flat_nesting() -> None:
    branches, nesting = _function_metrics(
        """
def route(value):
    if value == "a":
        return 1
    elif value == "b":
        return 2
    elif value == "c":
        return 3
    else:
        return 4
"""
    )

    assert branches == 3
    assert nesting == 1


def test_nested_if_still_increases_nesting() -> None:
    branches, nesting = _function_metrics(
        """
def route(value, enabled):
    if enabled:
        if value == "a":
            return 1
    return 0
"""
    )

    assert branches == 2
    assert nesting == 2
