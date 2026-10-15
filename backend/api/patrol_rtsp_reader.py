"""
单线程拉取 RTSP，缓存最新 JPEG 帧，供 /api/patrol/camera-mjpeg 多客户端复用。
"""
from __future__ import annotations

import threading
import time

import cv2
from django.conf import settings

_lock = threading.Lock()
_stop_evt = threading.Event()
_worker = None
_worker_url = None
_latest_jpeg = b""


def _run(url: str) -> None:
    global _latest_jpeg
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    while not _stop_evt.is_set():
        ok, frame = cap.read()
        if ok:
            enc_ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
            if enc_ok:
                with _lock:
                    _latest_jpeg = buf.tobytes()
        else:
            time.sleep(0.04)
    cap.release()


def ensure_rtsp_reader(url: str) -> None:
    global _worker, _worker_url, _stop_evt
    url = (url or "").strip()
    if not url:
        return
    with _lock:
        if _worker is not None and _worker.is_alive() and _worker_url == url:
            return
        _stop_evt.set()
        if _worker is not None:
            _worker.join(timeout=2.5)
        _stop_evt = threading.Event()
        _worker_url = url
        _worker = threading.Thread(target=_run, args=(url,), daemon=True, name="patrol-rtsp-reader")
        _worker.start()


def get_latest_jpeg() -> bytes:
    with _lock:
        return _latest_jpeg


def configured_rtsp_url() -> str:
    return (getattr(settings, "PATROL_RAW_CAMERA_RTSP_URL", None) or "").strip()
