"""Per-operator AST validation (Python, Phase 1).

Given the pre-patch and post-patch source of a file, plus a declared
BreakStep, confirm that the AST-level change at the declared line actually
corresponds to the declared mutation operator.

The check is deliberately approximate: we verify *category*, not exact node
equivalence. For example, OffByOne is accepted whenever any integer literal
near the anchor line changed by ±1.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from ..schema import BreakStep, MutationOp


@dataclass
class CheckResult:
    ok: bool
    reason: str


def _nodes_at_line(tree: ast.AST, line: int, radius: int = 2) -> list[ast.AST]:
    lo = line - radius
    hi = line + radius
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", None)
        if ln is None:
            continue
        if lo <= ln <= hi:
            out.append(node)
    return out


def _collect_int_constants(nodes: list[ast.AST]) -> list[int]:
    vals: list[int] = []
    for n in nodes:
        if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool):
            vals.append(n.value)
    return vals


def _collect_cmpops(nodes: list[ast.AST]) -> list[str]:
    kinds: list[str] = []
    for n in nodes:
        if isinstance(n, ast.Compare):
            for op in n.ops:
                kinds.append(type(op).__name__)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            kinds.append("Not")
    return kinds


def _collect_binops(nodes: list[ast.AST]) -> list[str]:
    kinds: list[str] = []
    for n in nodes:
        if isinstance(n, ast.BinOp):
            kinds.append(type(n.op).__name__)
        if isinstance(n, ast.BoolOp):
            kinds.append(type(n.op).__name__)
    return kinds


def _collect_call_args(nodes: list[ast.AST]) -> list[tuple[str, int]]:
    """Return (call_name, n_args) for each Call we see."""
    out: list[tuple[str, int]] = []
    for n in nodes:
        if isinstance(n, ast.Call):
            name = ""
            if isinstance(n.func, ast.Name):
                name = n.func.id
            elif isinstance(n.func, ast.Attribute):
                name = n.func.attr
            out.append((name, len(n.args)))
    return out


def _arg_texts(tree: ast.AST) -> list[list[str]]:
    """All positional-arg text lists for every Call in the tree."""
    out: list[list[str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            try:
                out.append([ast.unparse(arg) for arg in n.args])
            except Exception:
                pass
    return out


def _count(nodes: list[ast.AST], types: tuple[type, ...]) -> int:
    return sum(1 for n in nodes if isinstance(n, types))


def _parse_safe(src: str) -> ast.AST | None:
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def check_python(step: BreakStep, src_before: str, src_after: str) -> CheckResult:
    """Verify a Python BreakStep against pre- and post-patch source."""
    tree_before = _parse_safe(src_before)
    tree_after = _parse_safe(src_after)
    if tree_before is None:
        return CheckResult(False, "pre-patch source failed to parse")
    if tree_after is None:
        return CheckResult(False, "post-patch source failed to parse (structural break)")

    nodes_before = _nodes_at_line(tree_before, step.line)
    nodes_after = _nodes_at_line(tree_after, step.line)

    if step.op == MutationOp.OffByOne:
        a = sorted(_collect_int_constants(nodes_before))
        b = sorted(_collect_int_constants(nodes_after))
        if a == b:
            return CheckResult(False, "no integer literal delta near anchor")
        # Case 1: paired multisets differ by exactly 1 at some index
        for x, y in zip(a, b):
            if abs(x - y) == 1:
                return CheckResult(True, f"int literal {x}->{y}")
        # Case 2: a literal was inserted/removed (range(n) <-> range(n-1)).
        # The symmetric difference contains the inserted or removed value;
        # accept if that value is 1 (canonical off-by-one delta).
        sym = set(a) ^ set(b)
        if 1 in sym or -1 in sym:
            return CheckResult(True, "±1 literal inserted/removed")
        # Case 3: the literal set changed size and the diff element is small
        if abs(len(a) - len(b)) == 1:
            inserted = (set(b) - set(a)) if len(b) > len(a) else (set(a) - set(b))
            if any(abs(v) <= 1 for v in inserted):
                return CheckResult(True, "small literal delta")
        return CheckResult(False, "literal changed but not by ±1")

    if step.op == MutationOp.InvertedCondition:
        a = _collect_cmpops(nodes_before)
        b = _collect_cmpops(nodes_after)
        if a != b:
            return CheckResult(True, f"cmpops {a}->{b}")
        return CheckResult(False, "no comparison operator change at anchor")

    if step.op == MutationOp.WrongBinaryOperator:
        a = _collect_binops(nodes_before)
        b = _collect_binops(nodes_after)
        if a != b:
            return CheckResult(True, f"binops {a}->{b}")
        return CheckResult(False, "no binary operator change at anchor")

    if step.op == MutationOp.SwappedArgs:
        a = _collect_call_args(nodes_before)
        b = _collect_call_args(nodes_after)
        if a == b:
            before_args = _arg_texts(tree_before)
            after_args = _arg_texts(tree_after)
            for ba, aa in zip(before_args, after_args):
                if sorted(ba) == sorted(aa) and ba != aa and len(ba) >= 2:
                    return CheckResult(True, "positional args reordered")
            return CheckResult(False, "no argument reordering detected")
        return CheckResult(True, f"call shape changed {a}->{b}")

    # --- Remaining 11 operators --------------------------------------------

    if step.op == MutationOp.RemovedGuard:
        # delete of an If / Assert / Raise near the anchor
        before_count = _count(nodes_before, (ast.If, ast.Assert, ast.Raise))
        after_count = _count(_nodes_at_line(tree_after, step.line), (ast.If, ast.Assert, ast.Raise))
        if after_count < before_count:
            return CheckResult(True, f"guards {before_count}->{after_count}")
        return CheckResult(False, "no guard deleted at anchor")

    if step.op == MutationOp.DroppedReturn:
        # Return replaced by Pass / Return(None) / deleted
        before_returns = [n for n in nodes_before if isinstance(n, ast.Return)]
        after_returns = [n for n in _nodes_at_line(tree_after, step.line) if isinstance(n, ast.Return)]
        if len(before_returns) > len(after_returns):
            return CheckResult(True, "return statement removed")
        # Value dropped from a Return: before has non-None value, after has None/Pass
        for br, ar in zip(before_returns, after_returns):
            bv = br.value is not None
            av = ar.value is not None
            if bv and not av:
                return CheckResult(True, "return value dropped")
            if bv and av and ast.dump(br.value) != ast.dump(ar.value):
                # Detect specifically Return(Constant(None))
                if isinstance(ar.value, ast.Constant) and ar.value.value is None:
                    return CheckResult(True, "return value replaced by None")
        return CheckResult(False, "no return dropped at anchor")

    if step.op == MutationOp.SwitchedConstant:
        # Any Constant changed that is NOT covered by OffByOne (|Δ|≠1) and
        # not a bool (that's FlippedBoolean).
        def _consts(nodes: list[ast.AST]) -> list:
            out = []
            for n in nodes:
                if isinstance(n, ast.Constant) and not isinstance(n.value, bool):
                    out.append(n.value)
            return out
        a = _consts(nodes_before)
        b = _consts(_nodes_at_line(tree_after, step.line))
        if a == b:
            return CheckResult(False, "no constant change at anchor")
        # Exclude the pure OffByOne case
        if len(a) == len(b):
            for x, y in zip(sorted(a, key=str), sorted(b, key=str)):
                if isinstance(x, int) and isinstance(y, int) and abs(x - y) == 1:
                    continue
                return CheckResult(True, f"constant {x!r}->{y!r}")
            return CheckResult(False, "only ±1 deltas (OffByOne territory)")
        return CheckResult(True, "constant set changed")

    if step.op == MutationOp.FlippedBoolean:
        # A literal True/False constant flipped
        def _bools(nodes: list[ast.AST]) -> list[bool]:
            return [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, bool)]
        a = _bools(nodes_before)
        b = _bools(_nodes_at_line(tree_after, step.line))
        if a != b and sorted(a) != sorted(b):
            return CheckResult(True, f"bool {a}->{b}")
        # Also consider the NameConstant form: True/False used as a name
        return CheckResult(False, "no boolean literal flipped")

    if step.op == MutationOp.WrongExceptionType:
        # raise X(...) changed to raise Y(...), or except X changed to Y
        def _raise_types(nodes: list[ast.AST]) -> list[str]:
            out = []
            for n in nodes:
                if isinstance(n, ast.Raise) and n.exc is not None:
                    exc = n.exc
                    if isinstance(exc, ast.Call):
                        exc = exc.func
                    if isinstance(exc, ast.Name):
                        out.append(exc.id)
                    elif isinstance(exc, ast.Attribute):
                        out.append(exc.attr)
            return out

        def _except_types(nodes: list[ast.AST]) -> list[str]:
            out = []
            for n in nodes:
                if isinstance(n, ast.ExceptHandler) and n.type is not None:
                    t = n.type
                    if isinstance(t, ast.Name):
                        out.append(t.id)
                    elif isinstance(t, ast.Attribute):
                        out.append(t.attr)
            return out

        a_r = _raise_types(nodes_before)
        b_r = _raise_types(_nodes_at_line(tree_after, step.line))
        a_e = _except_types(nodes_before)
        b_e = _except_types(_nodes_at_line(tree_after, step.line))
        if a_r != b_r:
            return CheckResult(True, f"raise type {a_r}->{b_r}")
        if a_e != b_e:
            return CheckResult(True, f"except type {a_e}->{b_e}")
        return CheckResult(False, "no exception type change at anchor")

    if step.op == MutationOp.MissingAwait:
        # Await deleted: before has Await at anchor, after does not
        before_awaits = sum(1 for n in nodes_before if isinstance(n, ast.Await))
        after_awaits = sum(1 for n in _nodes_at_line(tree_after, step.line) if isinstance(n, ast.Await))
        if before_awaits > after_awaits:
            return CheckResult(True, f"await {before_awaits}->{after_awaits}")
        return CheckResult(False, "no await removed at anchor")

    if step.op == MutationOp.WrongLoopBound:
        # For.iter or While.test changed in a way that alters iteration count.
        # Accept if any of: For.iter changed, While.test changed, or an int
        # literal inside a For/While differs.
        def _loop_signatures(nodes: list[ast.AST]) -> list[str]:
            out = []
            for n in nodes:
                if isinstance(n, (ast.For, ast.While)):
                    try:
                        target = ast.unparse(n.iter) if isinstance(n, ast.For) else ast.unparse(n.test)
                    except Exception:
                        target = ""
                    out.append(target)
            return out
        a = _loop_signatures(nodes_before)
        b = _loop_signatures(_nodes_at_line(tree_after, step.line))
        if a != b:
            return CheckResult(True, f"loop bound {a}->{b}")
        return CheckResult(False, "no loop bound change at anchor")

    if step.op == MutationOp.StateReorder:
        # Adjacent statements permuted in the same block. Detect by comparing
        # the unparse of each Module/FunctionDef body near the anchor.
        def _body_signatures(tree: ast.AST) -> list[list[str]]:
            out = []
            for n in ast.walk(tree):
                if hasattr(n, "body") and isinstance(getattr(n, "body"), list):
                    body = getattr(n, "body")
                    for stmt in body:
                        ln = getattr(stmt, "lineno", -1)
                        if abs(ln - step.line) <= 3:
                            try:
                                out.append([ast.unparse(s) for s in body])
                            except Exception:
                                pass
                            break
            return out
        a = _body_signatures(tree_before)
        b = _body_signatures(tree_after)
        for ba, bb in zip(a, b):
            if sorted(ba) == sorted(bb) and ba != bb:
                return CheckResult(True, "adjacent statements reordered")
        return CheckResult(False, "no statement reorder detected at anchor")

    if step.op == MutationOp.ShadowedVariable:
        # A Store target near the anchor has its .id changed.
        def _store_names(nodes: list[ast.AST]) -> list[str]:
            out = []
            for n in nodes:
                if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Store):
                    out.append(n.id)
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            out.append(t.id)
            return out
        a = sorted(_store_names(nodes_before))
        b = sorted(_store_names(_nodes_at_line(tree_after, step.line)))
        if a != b:
            return CheckResult(True, f"store names {a}->{b}")
        return CheckResult(False, "no binding rename at anchor")

    if step.op == MutationOp.IncorrectTypeCast:
        # Call(func=int|str|float|list|tuple|set|dict|bool) inserted or removed
        CAST_NAMES = {"int", "str", "float", "list", "tuple", "set", "dict", "bool", "bytes"}
        def _casts(nodes: list[ast.AST]) -> int:
            n = 0
            for node in nodes:
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in CAST_NAMES:
                    n += 1
            return n
        a = _casts(nodes_before)
        b = _casts(_nodes_at_line(tree_after, step.line))
        if a != b:
            return CheckResult(True, f"cast count {a}->{b}")
        return CheckResult(False, "no type cast insertion/removal at anchor")

    if step.op == MutationOp.OmittedSideEffect:
        # An Expr(Call(...)) statement was deleted.
        def _expr_calls(nodes: list[ast.AST]) -> int:
            n = 0
            for node in nodes:
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    n += 1
            return n
        a = _expr_calls(nodes_before)
        b = _expr_calls(_nodes_at_line(tree_after, step.line))
        if a > b:
            return CheckResult(True, f"expr-calls {a}->{b}")
        return CheckResult(False, "no side-effect call removed at anchor")

    # Fallback: unknown operator → fail closed.
    return CheckResult(False, f"operator {step.op.value} not implemented for Python")
