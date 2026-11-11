"""OMP compatibility entry for the shared verified native protocol."""
import sys
from ..shared.omp_protocol import tools as _implementation
sys.modules[__name__] = _implementation
