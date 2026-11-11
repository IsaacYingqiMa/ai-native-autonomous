"""zscode binding to the verified shared native protocol."""
from ..shared.omp_protocol import installation as shared


def install(source_root, target_root, **options):
    return shared.install(source_root, target_root, **options, runtime="zscode")
