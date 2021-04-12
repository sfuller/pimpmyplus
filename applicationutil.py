# https://vintageapple.org/inside_r/pdf/PPC_System_Software_1994.pdf

from io import BytesIO
from typing import List

import rsrcfork


ARCH_68K = '68k'
ARCH_PPC = 'PPC'


def get_supported_archs(rsrc: bytes) -> List[str]:
    with BytesIO(rsrc) as f:
        resource_file = rsrcfork.ResourceFile(f)

    archs = []

    cfrg = resource_file.get(b'cfrg')
    if cfrg:
        # Assume it supports PPC if there's a cfrg lump.
        # TODO: Check the actuall processor field in this cfrg lump?
        archs.append(ARCH_PPC)

    code = resource_file.get(b'code')
    if code:
        archs.append(ARCH_68K)

    return archs

