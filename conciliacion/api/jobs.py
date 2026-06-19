"""Tracker simple de jobs en background (sync, ingest, reconcile son lentos)."""
from __future__ import annotations

import threading
import traceback
from datetime import datetime

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}


def snapshot() -> dict:
    with _LOCK:
        return {k: dict(v) for k, v in _JOBS.items()}


def is_running() -> bool:
    with _LOCK:
        return any(j["status"] == "running" for j in _JOBS.values())


def run(name: str, fn, *args, **kwargs) -> dict:
    """Lanza fn en un hilo. Devuelve el estado inicial del job."""
    with _LOCK:
        if any(j["status"] == "running" for j in _JOBS.values()):
            return {"name": name, "status": "rechazado",
                    "message": "Ya hay un proceso corriendo, espera a que termine."}
        _JOBS[name] = {"name": name, "status": "running", "message": "",
                       "started": datetime.now().isoformat(timespec="seconds"), "finished": None}

    def _target():
        try:
            msg = fn(*args, **kwargs) or "ok"
            _finish(name, "done", str(msg))
        except Exception as e:  # noqa
            _finish(name, "error", f"{e}\n{traceback.format_exc()[-600:]}")

    threading.Thread(target=_target, daemon=True).start()
    return dict(_JOBS[name])


def _finish(name, status, message):
    with _LOCK:
        _JOBS[name].update(status=status, message=message,
                           finished=datetime.now().isoformat(timespec="seconds"))
