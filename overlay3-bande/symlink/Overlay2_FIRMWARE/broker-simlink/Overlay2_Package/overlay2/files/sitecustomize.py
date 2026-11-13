__version__ = "1.0.0.(18) - overlay"
# -*- coding: utf-8 -*-
# =====================================================================================================================================
#      OVERLAY2
#      Name                       : OVERLAY2
#      Version                    : 1.0.0.(18)
#      Date Created               : 11-13-2026
#      Updated                    : 11-13-2026
#      Author                     : Saifuddin
#      Notes                      : sitecustomize.py (new file - does not modify any other code) - OVERLAY2 (persistent store) version.
#                                   config.json and conf.d/bridge.conf live on a separate persistent drive (USB ext4, mounted at
#                                   /mnt/persist) and are reached through symlinks from the broker folder. The root SD card is never
#                                   opened for writing (no remount).
# ======================================================================================================================================
"""
ATOMIC, power-cut safe writes for config.json, status.json, rfid1.json, rfid2.json,
tag_debounce_state.json and bridge.conf, without changing the application code.
Loaded automatically by Python through PYTHONPATH (set by setup_persist_store.sh on the send and ipservice drop-ins).

open(path, "w"/"wb") on a protected file becomes: write a temporary file in the SAME directory
-> flush + fsync -> compare with the current file (identical = nothing is replaced)
-> keep the previous good copy as <file>.bak (config.json and bridge.conf only) -> atomic rename -> fsync the directory.
If the process dies or an error occurs in the middle of a write, the old file stays intact.

Symlinks are resolved first, so a write to /home/alice/broker/config.json lands next to the real file on the persistent
drive (the temporary file and the rename are on the same filesystem). Only files whose real location is inside the
broker folder or inside the persistent folder are handled. Other modes (append, read, r+) and other files are NOT touched.
"""
import builtins
import io
import os

_PROTECTED = {"config.json", "status.json", "rfid1.json", "rfid2.json",
              "tag_debounce_state.json", "bridge.conf"}
_BACKUP = {"config.json", "bridge.conf"}          # these two keep a <name>.bak of the previous good version
_BROKER_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_PERSIST_DIR = os.path.realpath(os.environ.get("BROKER_PERSIST_DIR", "/mnt/persist"))
_BASES = (_BROKER_DIR, _PERSIST_DIR)
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
        full = os.path.realpath(os.path.abspath(p))     # follows symlinks (e.g. to the persistent drive)
    except Exception:
        return None
    for base in _BASES:
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


def _read_bytes(path):
    try:
        with _real_open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _keep_backup(target):
    """Copy the current file to <target>.bak (atomically) if it exists and is not empty."""
    try:
        old = _read_bytes(target)
        if not old:
            return
        bak = target + ".bak"
        tmpb = bak + ".tmp.%d" % os.getpid()
        with _real_open(tmpb, "wb") as f:
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmpb, bak)
    except OSError:
        try:
            os.remove(tmpb)
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
        attr = getattr(object.__getattribute__(self, "_f"), name)
        if callable(attr):
            owner = self   # keep the wrapper alive while a bound method (e.g. open(p, "w").write) is in use

            def _call(*a, **k):
                owner  # noqa: B018  (reference only)
                return attr(*a, **k)
            return _call
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
                new = _read_bytes(tmp)
                if new is not None and new == _read_bytes(target):
                    return                          # identical content: nothing to write (saves flash wear)
                if os.path.basename(target) in _BACKUP:
                    _keep_backup(target)
                try:   # keep the permissions and owner of the old file
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
