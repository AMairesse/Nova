from importlib import import_module

__all__ = [
    "SandboxShellResult",
    "exec_runner_is_configured",
    "exec_runner_is_enabled",
    "execute_sandbox_shell_command",
    "execute_sandbox_python_command",
    "test_exec_runner_access",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    service = import_module(".service", __name__)
    value = getattr(service, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
