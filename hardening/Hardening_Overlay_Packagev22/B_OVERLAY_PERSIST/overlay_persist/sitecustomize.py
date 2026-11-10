__version__ = "1.0.0.(16) - overlay"
# -*- coding: utf-8 -*-
# =====================================================================================================================================
#      OVERLAY
#      Name                       : OVERLAY
#      Version                    : 1.0.0.(16)
#      Date Created               : 11-10-2026
#      Updated                    : 11-10-2026
#      Author                     : Saifuddin
#      Notes                      : sitecustomize.py (new file - does not change any other code) - OVERLAY + PERSIST version
# ======================================================================================================================================
"""
1) ATOMIC writes for config.json, status.json, rfid1.json, rfid2.json,
   tag_debounce_state.json, bridge.conf (inside the broker folder only).
2) If the root filesystem is an OVERLAY: after config.json or bridge.conf is written,
   that file (and /etc/hostname for bridge.conf) is also copied to the real SD card through the helper
   /usr/local/sbin/broker-persist (SD remounted writable for a few milliseconds).
   status.json, rfid1.json, rfid2.json and logs/ are NOT copied: they stay in RAM and are lost at reboot.
Without the overlay, part (2) is automatically inactive.
"""
import builtins
import io
import os

_PROTECTED = {"config.json", "status.json", "rfid1.json", "rfid2.json",
              "tag_debounce_state.json", "bridge.conf"}
_PERSIST = {"config.json", os.path.join("conf.d", "bridge.conf")}     # only these two files (+ hostname through the helper)
_BROKER_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_LOWER = os.environ.get("BROKER_LOWER", "/media/root-ro")
_HELPER = os.environ.get("BROKER_PERSIST_HELPER", "/usr/local/sbin/broker-persist")
_real_open = io.open


def _overlay_active():
    if os.environ.get("BROKER_FORCE_OVERLAY") == "1":
        return True
    try:
        with _real_open("/proc/mounts") as f:
            for ln in f:
                p = ln.split()
                if len(p) > 2 and p[1] == "/" and p[2] == "overlay":
                    return True
    except Exception:
        pass
    return False


_OVERLAY = _overlay_active()


def _is_target(file, mode):
    if mode not in ("w", "wt", "tw", "wb", "bw"):
        return None
    if not isinstance(file, (str, bytes, os.PathLike)):
        return None
    try:
        p = os.fsdecode(file)
        if os.path.basename(p) not in _PROTECTED:
            return None
        full = os.path.realpath(os.path.abspath(p))
    except Exception:
        return None
    if not full.startswith(_BROKER_DIR + os.sep):
        return None
    return full


def _fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _persist(target):
    """Copy to the real SD card when needed. Never raises an error to the application."""
    try:
        rel = os.path.relpath(target, _BROKER_DIR)
        if rel not in _PERSIST:
            return
        import subprocess
        cmd = [_HELPER, rel] if os.geteuid() == 0 else ["sudo", "-n", _HELPER, rel]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        pass


class _AtomicFile(object):
    """File wrapper: every method is forwarded to the temporary file; committed on close()."""

    def __init__(self, target, tmp, f):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_tmp", tmp)
        object.__setattr__(self, "_f", f)
        object.__setattr__(self, "_done", False)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_f"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_f"), name, value)

    def __iter__(self):
        return iter(object.__getattribute__(self, "_f"))

    def _finish(self, commit):
        if object.__getattribute__(self, "_done"):
            return
        object.__setattr__(self, "_done", True)
        f = object.__getattribute__(self, "_f")
        tmp = object.__getattribute__(self, "_tmp")
        target = object.__getattribute__(self, "_target")
        committed = False
        try:
            if commit:
                f.flush()
                os.fsync(f.fileno())
            f.close()
            if commit:
                try:   # keep the permissions and owner of the old file
                    st = os.stat(target)
                    os.chmod(tmp, st.st_mode & 0o7777)
                    if os.geteuid() == 0:
                        os.chown(tmp, st.st_uid, st.st_gid)
                except OSError:
                    pass
                os.replace(tmp, target)
                _fsync_dir(os.path.dirname(target))
                committed = True
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if committed and _OVERLAY:
            _persist(target)

    def close(self):
        self._finish(True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._finish(exc_type is None)   # error in the middle -> discard, the old file stays intact
        return False

    def __del__(self):
        try:
            self._finish(True)
        except Exception:
            pass


def _open(file, mode="r", *args, **kwargs):
    target = _is_target(file, mode)
    if target is not None:
        try:
            d = os.path.dirname(target)
            tmp = os.path.join(d, "." + os.path.basename(target) + ".tmp.%d" % os.getpid())
            f = _real_open(tmp, mode, *args, **kwargs)
            return _AtomicFile(target, tmp, f)
        except Exception:
            pass   # whatever the problem: normal Python behaviour
    return _real_open(file, mode, *args, **kwargs)


builtins.open = _open
io.open = _open
