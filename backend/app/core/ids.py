"""UUIDv7 generation (RFC 9562 §5.7).

Architecture.md §15 calls for UUIDv7 primary keys on ``review_runs`` and
``findings``, for two reasons that pull in opposite directions and are both
satisfied by v7:

*   **Non-enumerable.** These ids appear in dashboard URLs and correlation
    logs. A ``BIGINT`` sequence tells anyone who sees one id how many runs
    exist and lets them walk to their neighbours' rows.
*   **Index-local.** A ``uuid4`` primary key scatters inserts across the whole
    B-tree, so every insert dirties a different page and the index never stays
    in cache. v7 puts a millisecond timestamp in the leading 48 bits, so ids
    generated near each other in time sort near each other — the insert
    locality of a sequence, without the enumerability.

Written here rather than taken from a dependency because it is 20 lines, and
adding a package for 20 lines contradicts ADR-0004 (dependency minimalism).
Python's own ``uuid`` module gained ``uuid7`` in 3.14; this is the same layout
and can be deleted when the runtime floor moves.

Layout::

    48 bits  unix_ts_ms      big-endian milliseconds since the epoch
     4 bits  version         0b0111
    12 bits  rand_a          random
     2 bits  variant         0b10
    62 bits  rand_b          random
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a time-ordered UUIDv7."""
    timestamp_ms = time.time_ns() // 1_000_000
    random_bytes = os.urandom(10)

    value = bytearray(16)
    value[0:6] = timestamp_ms.to_bytes(6, "big")
    value[6:16] = random_bytes

    # Version 7 in the high nibble of octet 6.
    value[6] = (value[6] & 0x0F) | 0x70
    # RFC 9562 variant (0b10) in the top two bits of octet 8.
    value[8] = (value[8] & 0x3F) | 0x80

    return uuid.UUID(bytes=bytes(value))


def uuid7_str() -> str:
    return str(uuid7())


__all__ = ["uuid7", "uuid7_str"]
