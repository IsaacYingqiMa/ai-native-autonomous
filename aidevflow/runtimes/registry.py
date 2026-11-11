"""Explicit runtime registration. SDK similarity never grants compatibility."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeAdapter:
    id: str
    executable: str
    adapter_version: str
    native_root: str | None
    reviewed_discovery: bool
    resource_redirection: bool
    enabled: bool = True
    tools_module: str | None = None
    recovery_module: str | None = None
    protocol: str = "claude-hooks"
    receipt_schema: str | None = None
    tested_version: str | None = None

    def inspect_session(self, root, session):
        import importlib
        module = self.recovery_module or "aidevflow.runtimes." + self.id.replace("-", "_") + ".observations"
        return importlib.import_module(module).inspect_session(root, session)

    def install(self, source_root, target_root, **options):
        import importlib
        module = importlib.import_module("aidevflow.runtimes." + self.id.replace("-", "_") + ".installation")
        return module.install(source_root, target_root, **options)

    def doctor(self, root):
        import importlib
        module = importlib.import_module("aidevflow.runtimes." + self.id.replace("-", "_") + ".diagnostics")
        return module.doctor(root)

    def preflight(self, root, payload):
        from ..gate import GateError
        import importlib
        if not self.tools_module:
            raise GateError("This runtime uses its own native preflight interface")
        return importlib.import_module(self.tools_module).preflight(root, payload)

    def recover_live_entries(self, root, payload):
        import importlib
        if not self.tools_module or not payload.get("entries"):
            return 0
        return importlib.import_module(self.tools_module).recover_live_entries(root, payload)

    def advisory_stop(self, root):
        import importlib
        if not self.tools_module:
            return None
        return importlib.import_module(self.tools_module).advisory_stop(root)

    def guard(self, root, payload):
        from ..gate import GateError
        import importlib
        if not self.tools_module:
            raise GateError("This runtime uses its own native hook interface")
        return importlib.import_module(self.tools_module).guard(root, payload)

    def observe(self, root, payload):
        import importlib
        return importlib.import_module("aidevflow.runtimes." + self.id.replace("-", "_") + ".observations").observe(root, payload)

    def recover(self, root, session=None):
        import importlib
        module = self.recovery_module or "aidevflow.runtimes." + self.id.replace("-", "_") + ".observations"
        return importlib.import_module(module).recover(root, session)

    def completion_hint(self):
        return ('Return through native yield as {"data": <the complete JSON object>}; the envelope is transport only.'
                if self.protocol == "omp-task-v1" else "Return exactly one complete JSON object through the native final-return mechanism.")


ADAPTERS = {
    "omp": RuntimeAdapter("omp", "omp", "omp-v1", ".omp", True, True, tools_module="aidevflow.runtimes.omp.tools", protocol="omp-task-v1", receipt_schema="aidevflow/invocation-v3", tested_version="18.13.2"),
    "claude-code": RuntimeAdapter("claude-code", "claude", "claude-observer-b2", ".claude", False, False, recovery_module="aidevflow.runtimes.claude_code.recovery"),
    "zscode": RuntimeAdapter("zscode", "zscode", "zscode-v1", ".zscode", True, True, tools_module="aidevflow.runtimes.zscode.tools", protocol="omp-task-v1", receipt_schema="aidevflow/invocation-v3", tested_version="0.1.0"),
}


def get_runtime(name):
    from ..gate import GateError
    adapter = ADAPTERS.get(name)
    if adapter is None or not adapter.enabled:
        raise GateError(f"Runtime {name!r} is not verified/available; complete native conformance before enabling it")
    return adapter
