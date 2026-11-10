# sitecustomize.py  (FILE BARU - tidak mengubah kode lain)  -- versi OVERLAY + PERSIST
# -*- coding: utf-8 -*-
"""
1) Penulisan ATOMIK untuk config.json, status.json, rfid1.json, rfid2.json,
   tag_debounce_state.json, bridge.conf (di folder broker saja).
2) Jika root filesystem berupa OVERLAY: setelah config.json atau bridge.conf ditulis,
   file itu (dan /etc/hostname untuk bridge.conf) juga disalin ke SD asli lewat helper
   /usr/local/sbin/broker-persist (remount tulis beberapa milidetik).
   status.json, rfid1.json, rfid2.json dan logs/ TIDAK disalin: tetap di RAM, hilang saat reboot.
Tanpa overlay, bagian (2) otomatis tidak aktif.
"""
import builtins
import io
import json
import os

_PROTECTED = {"config.json", "status.json", "rfid1.json", "rfid2.json",
              "tag_debounce_state.json", "bridge.conf"}
_PERSIST = {"config.json", os.path.join("conf.d", "bridge.conf")}     # hanya dua file ini (+ hostname lewat helper)
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


def _status_subset(path):
    try:
        with _real_open(path) as f:
            d = json.load(f)
        return {k: d.get(k) for k in _STATUS_KEYS}
    except Exception:
        return None


def _persist(target):
    """Salin ke SD asli bila perlu. Tidak pernah melempar error ke aplikasi."""
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
                try:
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
