# sitecustomize.py  -- OVERLAY 2 (volatile root, persistent data directory)
# -*- coding: utf-8 -*-
"""
Atomic writes (temp file + fsync + rename) for config.json, bridge.conf and the volatile
status files.

OVERLAY 2: config.json and conf.d/ inside the broker folder are SYMLINKS to a persistent data
directory (BROKER_PERSIST_DIR, default /boot/firmware/broker-data -- or a USB drive). open() is
patched, the symlink is resolved, and the temp file is created NEXT TO THE REAL FILE in that
persistent directory, so the rename is atomic on the same filesystem and the symlink is never
replaced. The root filesystem is never remounted and no helper / sudo is needed.
Files not in the persistent directory (status.json, rfid*.json, logs) stay in RAM (overlay).
"""
import builtins
import io
import os

_PROTECTED = {"config.json", "status.json", "rfid1.json", "rfid2.json",
              "tag_debounce_state.json", "bridge.conf"}
_BROKER_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_PERSIST_DIR = os.path.realpath(os.environ.get("BROKER_PERSIST_DIR", "/boot/firmware/broker-data"))
_ALLOWED = (_BROKER_DIR, _PERSIST_DIR)
_real_open = io.open


def _is_target(file, mode):
    if mode not in ("w", "wt", "tw", "wb", "bw"):
        return None
    if not isinstance(file, (str, bytes, os.PathLike)):
        return None
    try:
        p = os.fsdecode(file)
        if os.path.basename(p) not in _PROTECTED:
            return None
        full = os.path.realpath(os.path.abspath(p))      # follows the symlink to the persistent dir
    except Exception:
        return None
    for base in _ALLOWED:
        if full.startswith(base + os.sep):
            return full
    return None


def _fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class _AtomicFile(object):
    def __init__(self, target, tmp, f):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_tmp", tmp)
        object.__setattr__(self, "_f", f)
        object.__setattr__(self, "_done", False)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, "_f"), name)
        if callable(attr):
            owner = self            # keep this wrapper alive while the method runs, so that
            def _method(*a, **k):   # open(p, "w").write(x) is written BEFORE the commit
                return attr(*a, **k)
            _method._owner = owner
            return _method
        return attr

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
        try:
            if commit:
                f.flush()
                os.fsync(f.fileno())
            f.close()
            if commit:
                try:                                    # FAT has no unix permissions -> ignore errors
                    st = os.stat(target)
                    os.chmod(tmp, st.st_mode & 0o7777)
                    if os.geteuid() == 0:
                        os.chown(tmp, st.st_uid, st.st_gid)
                except OSError:
                    pass
                os.replace(tmp, target)
                _fsync_dir(os.path.dirname(target))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def close(self):
        self._finish(True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._finish(exc_type is None)
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
            pass
    return _real_open(file, mode, *args, **kwargs)


builtins.open = _open
io.open = _open
