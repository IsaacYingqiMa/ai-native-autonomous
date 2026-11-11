"""zscode binding to the verified shared native protocol."""
from ..shared.omp_protocol import observations as shared


def observe(root, payload):
    return shared.observe(root, payload, runtime="zscode")


def recover(root, session=None):
    return shared.recover(root, session, runtime="zscode")


def recover_branch(root, session, entries):
    return shared.recover_branch(root, session, entries, runtime="zscode")


def inspect_session(root, session):
    return shared.inspect_session(root, session, runtime="zscode")
