"""Compatibility import; implementation lives in its runtime package."""
import importlib
import sys
sys.modules[__name__] = importlib.import_module(".runtimes.omp.observations", __package__)
