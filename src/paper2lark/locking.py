"""Process-shared advisory locks for short local binding commits."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import time

from .errors import Paper2LarkError


@contextmanager
def exclusive(path, code='BINDING_BUSY', message='Another process is committing this profile binding; retry after it finishes.'):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            raise Paper2LarkError(code, message) from error
        yield
    finally:
        if locked:
            if os.name == 'nt':
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def library_lock(home, library_id):
    if not isinstance(library_id, str) or re.fullmatch(r'[0-9a-f]{32}', library_id) is None:
        raise Paper2LarkError('IDENTITY_INVALID', 'library_id must be 32 lowercase hexadecimal characters.')
    locks = Path(home) / 'locks'
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exclusive(locks / f'library-{library_id}.lock', 'LIBRARY_BUSY',
                   'Another process is updating this library; retry after it finishes.'):
        yield


@contextmanager
def wait_exclusive(path):
    """Acquire a process-shared advisory lock, waiting for a peer to finish."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            while not locked:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError:
                    time.sleep(0.01)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            if os.name == 'nt':
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def is_locked(path):
    """Return whether an existing advisory lock file is held by another caller."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    locked = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            return False
        except OSError:
            return True
    finally:
        if locked:
            if os.name == 'nt':
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
