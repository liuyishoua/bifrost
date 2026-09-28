"""Hold one data-directory lock on macOS, Linux, and Windows."""

import os


def acquire_exclusive(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        stream.write("0")
        stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
