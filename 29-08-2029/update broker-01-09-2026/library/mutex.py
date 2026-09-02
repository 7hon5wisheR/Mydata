# mutex.py
# -*- coding: utf-8 -*-
__version__ = "1.0.0(9)"
import fcntl
import time
import os

# =====================================================================================================================================
#      MUTEX
#      Name                       : MUTEX
#      Version                    : 1.0.0(9)
#      Date Created               : 05-05-2026
#      Updated                    : 08-05-2026
#      Changes                    : Added set_suppress_callback() and invoke suppress
#                                   at the start of on_message so lock/light workers in run.py
#                                   do not publish duplicate status (double/triple out)
# ======================================================================================================================================

class FileMutex:
    def __init__(self, name):
        self.name = name
        self.lockfile = f"/tmp/{name}.lock"
        self.fd = None

    def acquire(self, wait=True, retry_interval=3.0, timeout=None, owner="GENERIC"):

        start = time.time()
        attempt = 0

        # FIX (real bug): DO NOT reuse self.fd across acquire() calls.
        #
        # flock() locks belong to the OPEN FILE DESCRIPTION, not to the
        # process or to "this Python object". If self.fd is reused while
        # a previous acquire() is still holding the lock (release() not
        # yet called), a second acquire() attempt using that SAME fd is
        # not treated by the kernel as a new/competing lock request at
        # all - it's the exact same open file description re-confirming
        # a lock it already holds, so flock() succeeds IMMEDIATELY even
        # though a previous logical "acquire" is still active. This is
        # exactly what let two overlapping Startscan commands both print
        # "Mutex acquired" back-to-back with no "busy" in between, even
        # though the first scan was still running.
        #
        # Fix: open a brand new fd for EVERY acquire() attempt. Two
        # different fds (even from the same process/thread) ARE treated
        # as independent competing lock requests by flock(), so the
        # second one correctly gets BlockingIOError while the first
        # fd's lock is still held. self.fd is only set AFTER a successful
        # acquire, so release() always unlocks/closes the fd that
        # actually won the lock.
        fd = open(self.lockfile, "w")

        while True:
            try:

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


                fd.seek(0)
                fd.truncate()
                fd.write(f"{owner} PID={os.getpid()}\n")
                fd.flush()

                self.fd = fd
                print(f"[{owner}] Mutex acquired ({self.lockfile})")
                return True

            except BlockingIOError:
                attempt += 1

                if not wait:
                    print(f"[{owner}] Mutex busy, not waiting")
                    fd.close()
                    return False


                holder = "unknown"
                try:
                    with open(self.lockfile, "r") as f:
                        holder = f.read().strip() or "unknown"
                except Exception:
                    pass


                print(
                    f"[{owner}] Busy mutex active, waiting... "
                    f"(retry {attempt}) holder=[{holder}]"
                )

                if timeout and (time.time() - start) > timeout:
                    print(f"[{owner}] Mutex timeout after {attempt} retries")
                    fd.close()
                    return False


                time.sleep(retry_interval)

    def release(self, owner="GENERIC"):
        if self.fd:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                self.fd.close()
                self.fd = None
                print(f"[{owner}] Mutex released ({self.lockfile})")
