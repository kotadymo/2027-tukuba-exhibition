from __future__ import annotations

import logging
import os
import queue
import shutil
import socket
import struct
import subprocess
import threading
import time
from collections import deque

import cv2
import numpy as np


logger = logging.getLogger(__name__)

_SYNC = b"\x06\x00\x00syNceNdinS"
_KEEPALIVE = b"\x05\x00\x00"
_COMMAND = b"\x04\x00\x00"
_VIDEO = b"\x01\x00\x00"
_VIDEO_HEADER_SIZE = 12
_MAX_PACKET_SIZE = 32 * 1024 * 1024
_MAX_JPEG_BUFFER_SIZE = 32 * 1024 * 1024


class Insta360SourceError(RuntimeError):
    pass


def _pack_packet(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload) + 4) + payload


def _command_packet(command_id: int, sequence: int, proto: bytes = b"") -> bytes:
    header = (
        _COMMAND
        + struct.pack("<H", command_id)
        + b"\x02"
        + struct.pack("<I", sequence)[:3]
        + b"\x80\x00\x00"
    )
    return _pack_packet(header + proto)


class _FFmpegDecoder:
    def __init__(self, executable: str):
        self._process = subprocess.Popen(
            [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags",
                "nobuffer",
                "-flags",
                "low_delay",
                "-f",
                "hevc",
                "-i",
                "pipe:0",
                "-an",
                "-sn",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                "3",
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._stderr_lines: deque[str] = deque(maxlen=8)
        self._lock = threading.Lock()
        self._reader_error: str | None = None
        self._reader_stopped = threading.Event()
        self._stdout_thread = threading.Thread(
            target=self._read_frames, name="insta360-ffmpeg-frames", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_errors, name="insta360-ffmpeg-errors", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def feed(self, data: bytes) -> None:
        return_code = self._process.poll()
        if return_code is not None:
            raise Insta360SourceError(self._process_error(return_code))
        if self._process.stdin is None:
            raise Insta360SourceError("FFmpeg の入力ストリームが利用できません")
        try:
            self._process.stdin.write(data)
            self._process.stdin.flush()
        except OSError as exc:
            raise Insta360SourceError(
                f"FFmpeg に H.265 映像を渡せません: {self._last_error()}"
            ) from exc

    def read_frame(self) -> np.ndarray | None:
        return_code = self._process.poll()
        if return_code is not None:
            raise Insta360SourceError(self._process_error(return_code))
        with self._lock:
            reader_error = self._reader_error
        if reader_error:
            raise Insta360SourceError(f"FFmpeg の映像出力を読み取れません: {reader_error}")
        if self._reader_stopped.is_set():
            raise Insta360SourceError("FFmpeg の映像出力が停止しました")
        try:
            return self._frames.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except OSError:
                logger.debug("FFmpeg input stream was already closed")
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._stdout_thread.join(timeout=1)
        self._stderr_thread.join(timeout=1)
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.stderr is not None:
            self._process.stderr.close()

    def _read_frames(self) -> None:
        buffer = bytearray()
        try:
            if self._process.stdout is None:
                raise Insta360SourceError("FFmpeg の出力ストリームがありません")
            while True:
                chunk = self._process.stdout.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        if len(buffer) > 1:
                            del buffer[:-1]
                        break
                    if start:
                        del buffer[:start]
                    end = buffer.find(b"\xff\xd9", 2)
                    if end < 0:
                        break
                    jpeg = bytes(buffer[: end + 2])
                    del buffer[: end + 2]
                    image = cv2.imdecode(
                        np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if image is not None:
                        self._offer_latest(image)
                if len(buffer) > _MAX_JPEG_BUFFER_SIZE:
                    raise Insta360SourceError("FFmpeg の JPEG 出力が上限を超えました")
        except Exception as exc:
            with self._lock:
                self._reader_error = str(exc)
            logger.exception("Could not read decoded Insta360 frames")
        finally:
            self._reader_stopped.set()

    def _read_errors(self) -> None:
        if self._process.stderr is None:
            return
        for line in iter(self._process.stderr.readline, b""):
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                with self._lock:
                    self._stderr_lines.append(message)
                logger.warning("FFmpeg: %s", message)

    def _offer_latest(self, image: np.ndarray) -> None:
        try:
            self._frames.put_nowait(image)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frames.put_nowait(image)
            except queue.Full:
                logger.warning("Dropping a decoded Insta360 frame")

    def _last_error(self) -> str:
        with self._lock:
            return " / ".join(self._stderr_lines) or "詳細情報はありません"

    def _process_error(self, return_code: int) -> str:
        return f"FFmpeg が終了しました (code {return_code}): {self._last_error()}"


class Insta360Source:
    def __init__(
        self,
        camera_ip: str,
        tcp_port: int,
        ffmpeg_path: str | None = None,
    ):
        executable = ffmpeg_path or os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
        if executable is None:
            raise Insta360SourceError(
                "FFmpeg が見つかりません。FFmpeg をインストールするか FFMPEG_PATH を設定してください"
            )

        self._camera_ip = camera_ip
        self._tcp_port = tcp_port
        self._socket: socket.socket | None = None
        self._decoder: _FFmpegDecoder | None = None
        self._packet_buffer = bytearray()
        self._last_keepalive = time.monotonic()
        self._closed = False
        try:
            self._decoder = _FFmpegDecoder(executable)
            self._socket = socket.create_connection((camera_ip, tcp_port), timeout=5)
            self._socket.settimeout(0.1)
            self._send(_SYNC)
            self._send(_KEEPALIVE)
            now_ms = int(time.time() * 1000)
            time_proto = (
                b"\x0a\x02\x0c\x0d\x12\x06" + struct.pack("<q", now_ms)[:6]
            )
            self._send(_command_packet(7, 0, time_proto))
            time.sleep(0.3)
            self._send(_command_packet(1, 1))
            self._last_keepalive = time.monotonic()
        except Exception as exc:
            self.close()
            if isinstance(exc, Insta360SourceError):
                raise
            raise Insta360SourceError(
                f"Insta360 GO Ultra に接続できません "
                f"({camera_ip}:{tcp_port}): {exc}"
            ) from exc

    def read_frame(self, timeout: float = 0.1) -> np.ndarray | None:
        if self._closed or self._socket is None or self._decoder is None:
            raise Insta360SourceError("Insta360 ソースは切断されています")
        deadline = time.monotonic() + max(0, timeout)
        while True:
            frame = self._decoder.read_frame()
            if frame is not None:
                return frame
            if time.monotonic() >= deadline:
                return None
            now = time.monotonic()
            if now - self._last_keepalive >= 2:
                self._send(_KEEPALIVE)
                self._last_keepalive = now
            try:
                chunk = self._socket.recv(131072)
            except socket.timeout:
                continue
            except OSError as exc:
                raise Insta360SourceError(f"カメラ映像の受信に失敗しました: {exc}") from exc
            if not chunk:
                raise Insta360SourceError("カメラとの接続が切断されました")
            self._packet_buffer.extend(chunk)
            self._process_packets()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._socket is not None:
            try:
                self._send(_command_packet(2, 2))
            except OSError:
                logger.debug("Could not send the Insta360 stop-stream command")
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                logger.debug("Insta360 socket was already closed")
            self._socket.close()
            self._socket = None
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None

    def _send(self, payload: bytes) -> None:
        if self._socket is None:
            raise Insta360SourceError("Insta360 ソケットがありません")
        self._socket.sendall(_pack_packet(payload))

    def _process_packets(self) -> None:
        while len(self._packet_buffer) >= 4:
            packet_length = struct.unpack_from("<I", self._packet_buffer)[0]
            if packet_length < 4 or packet_length > _MAX_PACKET_SIZE:
                raise Insta360SourceError(
                    f"カメラから不正なパケット長を受信しました: {packet_length}"
                )
            if len(self._packet_buffer) < packet_length:
                return
            payload = bytes(self._packet_buffer[4:packet_length])
            del self._packet_buffer[:packet_length]
            if payload.startswith(_VIDEO):
                if len(payload) <= _VIDEO_HEADER_SIZE:
                    continue
                if self._decoder is None:
                    raise Insta360SourceError("FFmpeg デコーダーが利用できません")
                self._decoder.feed(payload[_VIDEO_HEADER_SIZE:])

        if len(self._packet_buffer) > _MAX_PACKET_SIZE:
            raise Insta360SourceError("カメラの受信バッファが上限を超えました")
