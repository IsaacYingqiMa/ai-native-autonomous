"""Compatibility alias; diagnostic implementations belong to runtime providers."""
import sys
from .runtimes.claude_code import diagnostics as _implementation
sys.modules[__name__] = _implementation
