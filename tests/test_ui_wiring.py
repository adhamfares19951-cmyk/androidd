"""
Static wiring check between the GUI (ui/) and the core objects it calls, and between core modules
and ADBBridge. It found real crashes that no other test reached because nothing pressed those
buttons: calls to methods that do not exist, wrong keyword arguments, unpacking 2 values from a
function returning 3, and reading result fields that the result class does not have.

For every `self.<obj>.<method>(...)` where <obj> is a known core object, it checks:
1. the method exists;
2. the call's positional count and keyword names bind to the method's signature;
3. `a, b = self.<obj>.<method>(...)` unpacks as many values as the Tuple[...] return annotation;
4. `x = self.<obj>.<method>(...)` followed by `x.<field>` / `x[i].<field>` in the same function uses
   fields that exist on the returned dataclass (also for List[...] of dataclasses).
"""
import ast
import dataclasses
import glob
import inspect
import os
import typing
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _classes():
    from core.adb_bridge import ADBBridge
    from core.batch_apk_manager import BatchAPKManager
    from core.board_diag import MotherboardDiagnostics
    from core.hardware_tester import HardwareTester
    from core.malware_hunter import MalwareHunter
    from core.optimizer import RealSystemOptimizer
    from core.partition_backup import PartitionBackupManager
    from core.profiler import DeviceProfiler
    from core.provisioning_manager import ProvisioningManager
    from core.rescue_center import RescueCenter
    from core.storage_benchmark import StorageBenchmark
    from core.touch_matrix import TouchMatrixTester
    from database.db_manager import DatabaseManager
    return {
        "db": DatabaseManager, "bridge": ADBBridge, "profiler": DeviceProfiler,
        "board_diag": MotherboardDiagnostics, "hardware_tester": HardwareTester,
        "malware_hunter": MalwareHunter, "optimizer": RealSystemOptimizer, "rescue_center": RescueCenter,
        "provisioning": ProvisioningManager, "storage_bench": StorageBenchmark,
        "partition_backup": PartitionBackupManager, "batch_apk": BatchAPKManager, "touch_matrix": TouchMatrixTester,
    }


def _target(call):
    """('obj', 'method') for self.<obj>.<method>(...), else None."""
    f = call.func
    if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)
            and isinstance(f.value.value, ast.Name) and f.value.value.id == "self"):
        return f.value.attr, f.attr
    return None


def _return_info(func):
    """(tuple_length or None, dataclass or None, is_list)."""
    try:
        hint = typing.get_type_hints(func).get("return")
    except Exception:
        return None, None, False
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin is tuple and args and args[-1] is not Ellipsis:
        return len(args), None, False
    if origin is list and args and dataclasses.is_dataclass(args[0]):
        return None, args[0], True
    if dataclasses.is_dataclass(hint):
        return None, hint, False
    return None, None, False


def _fields(cls):
    names = {f.name for f in dataclasses.fields(cls)}
    names |= {n for n, _ in inspect.getmembers(cls) if not n.startswith("__")}
    return names


def _instance_attrs(cls):
    """Attributes assigned as self.<name> in __init__ (they are not visible on the class)."""
    try:
        src = inspect.getsource(cls.__init__)
    except (TypeError, OSError):
        return set()
    import re
    return set(re.findall(r"self\.(\w+)\s*(?::[^=]+)?=", src))


def _check_attribute_reads(tree, rel, classes, problems):
    """self.<obj>.<name> must exist on the class or be set in its __init__."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name) and node.value.value.id == "self"
                and node.value.attr in classes):
            cls = classes[node.value.attr]
            if not hasattr(cls, node.attr) and node.attr not in _instance_attrs(cls):
                problems.append(f"{rel}:{node.lineno} self.{node.value.attr}.{node.attr}: {cls.__name__} has no attribute {node.attr}")


def _check_file(path, classes, problems):
    rel = os.path.relpath(path, ROOT)
    tree = ast.parse(open(path, encoding="utf-8").read())
    _check_attribute_reads(tree, rel, classes, problems)
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        results = {}  # local name -> (dataclass, is_list)
        for node in ast.walk(func):
            call, targets = None, None
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call, targets = node.value, node.targets
            elif isinstance(node, ast.Call):
                call = node
            if call is None:
                continue
            tgt = _target(call)
            if not tgt or tgt[0] not in classes:
                continue
            obj, meth = tgt
            cls = classes[obj]
            where = f"{rel}:{call.lineno} self.{obj}.{meth}"
            fn = getattr(cls, meth, None)
            if fn is None or not callable(fn):
                problems.append(f"{where}: {cls.__name__} has no method {meth}")
                continue
            if not any(isinstance(a, ast.Starred) for a in call.args) and not any(k.arg is None for k in call.keywords):
                try:
                    inspect.signature(fn).bind(None, *[None] * len(call.args), **{k.arg: None for k in call.keywords})
                except TypeError as e:
                    problems.append(f"{where}: arguments do not match the signature ({e})")
            n_tuple, dc, is_list = _return_info(fn)
            if targets and len(targets) == 1 and isinstance(targets[0], ast.Tuple) and n_tuple is not None:
                if len(targets[0].elts) != n_tuple:
                    problems.append(f"{where}: unpacks {len(targets[0].elts)} values, returns {n_tuple}")
            if targets and len(targets) == 1 and isinstance(targets[0], ast.Name) and dc is not None:
                results[targets[0].id] = (dc, is_list)
        for node in ast.walk(func):
            if not isinstance(node, ast.Attribute):
                continue
            base = node.value
            if isinstance(base, ast.Subscript):
                base = base.value
                indexed = True
            else:
                indexed = False
            if isinstance(base, ast.Name) and base.id in results:
                dc, is_list = results[base.id]
                if is_list != indexed:
                    continue
                if node.attr not in _fields(dc):
                    problems.append(f"{rel}:{node.lineno} {base.id}.{node.attr}: {dc.__name__} has no field {node.attr}")


class WiringTests(unittest.TestCase):
    def test_gui_calls_match_core(self):
        classes = _classes()
        problems = []
        for path in sorted(glob.glob(os.path.join(ROOT, "ui", "**", "*.py"), recursive=True)):
            _check_file(path, classes, problems)
        problems = sorted(set(problems))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_core_calls_to_the_bridge_match(self):
        from core.adb_bridge import ADBBridge
        problems = []
        for path in sorted(glob.glob(os.path.join(ROOT, "core", "*.py"))) + [os.path.join(ROOT, "diagnostics_runner.py")]:
            _check_file(path, {"bridge": ADBBridge}, problems)
        problems = sorted(set(problems))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()


def _main_block_names(path):
    """Names bound only inside `if __name__ == "__main__":` (they do not exist when the module is imported)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    names.add(sub.id)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    names |= {(a.asname or a.name).split(".")[0] for a in sub.names}
    return names


class UndefinedNameTests(unittest.TestCase):
    """Every global name a module's functions use must exist (catches a missing import before it runs)."""

    def test_no_undefined_globals(self):
        import builtins
        import importlib
        import symtable

        def global_refs(table):
            out = {s.get_name() for s in table.get_symbols() if s.is_global() and s.is_referenced()}
            for child in table.get_children():
                out |= global_refs(child)
            return out

        files = (glob.glob(os.path.join(ROOT, "core", "*.py")) + glob.glob(os.path.join(ROOT, "ui", "**", "*.py"), recursive=True)
                 + glob.glob(os.path.join(ROOT, "database", "*.py"))
                 + [os.path.join(ROOT, f) for f in ("main.py", "diagnostics_runner.py", "app_paths.py", "setup_bins.py")])
        problems = []
        for path in sorted(files):
            rel = os.path.relpath(path, ROOT)
            mod = importlib.import_module(rel[:-3].replace(os.sep, "."))
            table = symtable.symtable(open(path, encoding="utf-8").read(), path, "exec")
            main_only = _main_block_names(path)
            for name in sorted(global_refs(table)):
                if not hasattr(mod, name) and not hasattr(builtins, name) and name not in main_only:
                    problems.append(f"{rel}: undefined name {name}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))
