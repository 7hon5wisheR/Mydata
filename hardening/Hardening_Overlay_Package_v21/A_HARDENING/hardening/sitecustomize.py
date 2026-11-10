# sitecustomize.py  (FILE BARU - tidak mengubah kode lain)
# -*- coding: utf-8 -*-
"""
Membuat penulisan file data broker ATOMIK dan tahan mati lampu, tanpa mengubah
kode aplikasi. Dimuat otomatis oleh Python lewat PYTHONPATH (diatur oleh
harden_sd.sh pada send.service dan ipservice.service).

Untuk file: config.json, status.json, rfid1.json, rfid2.json,
            tag_debounce_state.json, bridge.conf  (di dalam folder broker saja)
open(path, "w"/"wb") diganti: tulis ke file sementara -> fsync -> rename.
Jika proses mati / terjadi error di tengah penulisan, file lama tetap utuh.
Mode lain (append, baca, r+) dan file lain TIDAK disentuh.
"""
import builtins
import io
import os

_PROTECTED = {"config.json", "status.json", "rfid1.json", "rfid2.json",
              "tag_debounce_state.json", "bridge.conf"}
_BROKER_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
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


class _AtomicFile(object):
    """Pembungkus file: semua method diteruskan ke file sementara; commit saat close()."""

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
        try:
            if commit:
                f.flush()
                os.fsync(f.fileno())
            f.close()
            if commit:
                try:   # pertahankan izin & pemilik file lama
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
        self._finish(exc_type is None)   # ada error di tengah -> buang, file lama utuh
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
            pass   # apa pun masalahnya: perilaku normal Python
    return _real_open(file, mode, *args, **kwargs)


builtins.open = _open
io.open = _open
