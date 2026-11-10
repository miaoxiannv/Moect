"""Atomic file replacement with bounded retries for transient Windows locks."""
import os
import time


def replace(source, destination):
    for attempt in range(8):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 7:
                raise
            time.sleep(min(0.1 * (2 ** attempt), 1.0))
