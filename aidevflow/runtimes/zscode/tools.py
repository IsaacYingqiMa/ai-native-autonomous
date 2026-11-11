"""zscode binding to the verified shared native protocol."""
from ..shared.omp_protocol import tools as shared


def guard(root, payload):
    return shared.guard(root, payload, runtime="zscode")


def preflight(root, payload):
    return shared.preflight(root, payload, runtime="zscode")


def recover_live_entries(root, payload):
    return shared.recover_live_entries(root, payload, runtime="zscode")


def advisory_stop(root):
    return shared.advisory_stop(root)
