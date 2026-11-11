"""zscode binding to the verified shared native protocol."""
from ..shared.omp_protocol import diagnostics as shared


def doctor(root):
    return shared.doctor(root, runtime="zscode")
