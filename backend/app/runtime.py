from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import struct
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

from .database import (
    AlertRule,
    AppSetting,
    Insta360Settings,
    MODELS_DIR,
    ModelRecord,
    ModelSettings,
    SessionLocal,
    SourceSettings,
    VIDEOS_DIR,
    Video,
)
from .alert_storage import persist_alert_frame_events
from .inference import Detection, Detector
from .insta360_source import Insta360Source, Insta360SourceError


logger = logging.getLogger(__name__)


@dataclass
class RuleState:
    state: str = "idle"
    pending_since: float | None = None
    hit_count: int = 0
    missing_since: float | None = None
    last_alert_at: float | None = None


class RuleEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._rules: dict[int, dict[str, Any]] = {}
        self._states: dict[int, RuleState] = {}
        self._active_classes: set[str] = set()
        self._last_frame_time: float | None = None

    def configure(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            cleared = self._clear_active_locked("rules_changed")
            self._rules = {rule["id"]: rule for rule in rules}
            self._states = {rule_id: RuleState() for rule_id in self._rules}
            self._last_frame_time = None
            return cleared

    def set_model_classes(self, class_names: set[str]) -> list[dict[str, Any]]:
        with self._lock:
            cleared = self._clear_active_locked("model_changed")
            self._active_classes = set(class_names)
            self._states = {rule_id: RuleState() for rule_id in self._rules}
            self._last_frame_time = None
            return cleared

    def reset(self, clear_cooldown: bool, reason: str) -> list[dict[str, Any]]:
        with self._lock:
            events = self._clear_active_locked(reason)
            for state in self._states.values():
                state.state = "idle"
                state.pending_since = None
                state.hit_count = 0
                state.missing_since = None
                if clear_cooldown:
                    state.last_alert_at = None
            self._last_frame_time = None
            return events

    def statuses(self) -> dict[int, dict[str, Any]]:
        with self._lock:
            return {
                rule_id: {
                    "state": state.state,
                    "incompatible": bool(
                        set(rule["class_names"]) - self._active_classes
                    ),
                    "missing_class_names": sorted(
                        set(rule["class_names"]) - self._active_classes
                    ),
                }
                for rule_id, rule in self._rules.items()
                for state in [self._states[rule_id]]
            }

    def evaluate(
        self,
        detections: list[Detection],
        frame_time: float,
        source_type: str,
        video_id: int | None,
        media_time_ms: int | None,
        model_id: int | None,
    ) -> tuple[list[int], list[dict[str, Any]]]:
        with self._lock:
            events: list[dict[str, Any]] = []
            if (
                self._last_frame_time is not None
                and (frame_time < self._last_frame_time or frame_time - self._last_frame_time > 2.0)
            ):
                events.extend(self._reset_locked(clear_cooldown=False, reason="stream_gap"))
            self._last_frame_time = frame_time
            observed = {detection.class_name for detection in detections}
            matched_rule_ids = []

            for rule_id, rule in self._rules.items():
                state = self._states[rule_id]
                required = set(rule["class_names"])
                if not rule["enabled"] or required - self._active_classes:
                    continue
                condition_met = required.issubset(observed)
                if condition_met:
                    matched_rule_ids.append(rule_id)
                    state.missing_since = None
                    if state.state == "active":
                        continue
                    if state.state == "idle":
                        state.state = "pending"
                        state.pending_since = frame_time
                        state.hit_count = 1
                    else:
                        state.hit_count += 1

                    duration_met = (
                        state.pending_since is not None
                        and frame_time - state.pending_since
                        >= rule["detection_duration_ms"] / 1000
                        and state.hit_count >= 2
                    )
                    cooldown_met = (
                        state.last_alert_at is None
                        or frame_time - state.last_alert_at >= rule["cooldown_ms"] / 1000
                    )
                    if duration_met and cooldown_met:
                        state.state = "active"
                        state.last_alert_at = frame_time
                        events.append(
                            {
                                "type": "alert_started",
                                "rule_id": rule_id,
                                "rule_name": rule["name"],
                                "detected_classes": rule["class_names"],
                                "timestamp": datetime.now().astimezone().isoformat(),
                                "source_type": source_type,
                                "video_id": video_id,
                                "media_time_ms": media_time_ms,
                                "model_id": model_id,
                                "test": False,
                            }
                        )
                elif state.state == "pending":
                    state.state = "idle"
                    state.pending_since = None
                    state.hit_count = 0
                elif state.state == "active":
                    if state.missing_since is None:
                        state.missing_since = frame_time
                    elif (
                        frame_time - state.missing_since
                        >= rule["release_duration_ms"] / 1000
                    ):
                        state.state = "idle"
                        state.pending_since = None
                        state.hit_count = 0
                        state.missing_since = None
                        events.append(
                            {
                                "type": "alert_cleared",
                                "rule_id": rule_id,
                                "rule_name": rule["name"],
                                "reason": "condition_released",
                            }
                        )
            return matched_rule_ids, events

    def _clear_active_locked(self, reason: str) -> list[dict[str, Any]]:
        events = []
        for rule_id, state in self._states.items():
            if state.state == "active":
                rule = self._rules[rule_id]
                events.append(
                    {
                        "type": "alert_cleared",
                        "rule_id": rule_id,
                        "rule_name": rule["name"],
                        "reason": reason,
                    }
                )
        return events

    def _reset_locked(self, clear_cooldown: bool, reason: str) -> list[dict[str, Any]]:
        events = self._clear_active_locked(reason)
        for state in self._states.values():
            state.state = "idle"
            state.pending_since = None
            state.hit_count = 0
            state.missing_since = None
            if clear_cooldown:
                state.last_alert_at = None
        return events


@dataclass(eq=False)
class Subscriber:
    events: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    frames: asyncio.Queue[bytes] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1)
    )
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class WebSocketHub:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[Subscriber] = set()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> Subscriber:
        subscriber = Subscriber()
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    async def receive(self, subscriber: Subscriber) -> str | bytes:
        while True:
            if not subscriber.events.empty():
                return subscriber.events.get_nowait()
            if not subscriber.frames.empty():
                return subscriber.frames.get_nowait()
            subscriber.ready.clear()
            await subscriber.ready.wait()

    def publish_event(self, event: dict[str, Any]) -> None:
        message = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        self._dispatch(self._publish_event, message)

    def publish_frame(self, frame: bytes) -> None:
        self._dispatch(self._publish_frame, frame)

    def _dispatch(self, callback, value) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            callback(value)
        else:
            loop.call_soon_threadsafe(callback, value)

    def _publish_event(self, message: str) -> None:
        for subscriber in tuple(self._subscribers):
            subscriber.events.put_nowait(message)
            subscriber.ready.set()

    def _publish_frame(self, frame: bytes) -> None:
        for subscriber in tuple(self._subscribers):
            if subscriber.frames.full():
                try:
                    subscriber.frames.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            subscriber.frames.put_nowait(frame)
            subscriber.ready.set()


@dataclass(frozen=True)
class FrameInput:
    frame_id: int
    source_session_id: str
    image: Any
    source_type: str
    timestamp: str
    captured_at: float
    video_id: int | None = None
    media_time_ms: int | None = None


class CaptureController:
    def __init__(self, on_frame, on_event, on_reset):
        self._on_frame = on_frame
        self._on_event = on_event
        self._on_reset = on_reset
        self._condition = threading.Condition()
        self._stop = False
        self._generation = 0
        self._source_type = "insta360"
        self._camera_ip = "192.168.42.1"
        self._tcp_port = 6666
        self._reconnect_interval_ms = 3000
        self._source_session_id = uuid.uuid4().hex
        self._connect_requested = False
        self._video_id: int | None = None
        self._video_path: Path | None = None
        self._video_fps: float | None = None
        self._video_duration_ms: int | None = None
        self._loop_enabled = True
        self._playback_state = "ready"
        self._position_ms = 0
        self._insta360_status = "disconnected"
        self._insta360_error: str | None = None
        self._last_frame_received_at: str | None = None
        self._video_error: str | None = None
        self._frame_id = 0
        self._thread = threading.Thread(target=self._run, name="video-capture", daemon=True)
        self._applied_generation = -1

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        self._thread.join(timeout=5)

    def select_source(self, source_type: str) -> None:
        with self._condition:
            if source_type not in {"insta360", "video"}:
                raise ValueError(f"Unsupported source type: {source_type}")
            changed = self._source_type != source_type
            if not changed:
                return
            self._generation += 1
            self._source_type = source_type
            self._connect_requested = False
            self._video_id = None
            self._video_path = None
            self._playback_state = "ready"
            self._position_ms = 0
            self._source_session_id = uuid.uuid4().hex
            self._insta360_status = "disconnected"
            self._insta360_error = None
            self._last_frame_received_at = None
            self._video_error = None
            self._condition.notify_all()
        self._on_reset(True, "source_changed")
        self._on_event({"type": "source_changed", "source_type": source_type})
        self._emit_insta360_status()

    def configure_insta360(
        self,
        camera_ip: str,
        tcp_port: int,
        reconnect_interval_ms: int,
    ) -> None:
        with self._condition:
            changed = (
                self._camera_ip != camera_ip
                or self._tcp_port != tcp_port
                or self._reconnect_interval_ms != reconnect_interval_ms
            )
            if not changed:
                return
            self._camera_ip = camera_ip
            self._tcp_port = tcp_port
            self._reconnect_interval_ms = reconnect_interval_ms
            if self._connect_requested:
                self._generation += 1
                self._source_session_id = uuid.uuid4().hex
                self._insta360_status = "connecting"
            self._insta360_error = None
            self._condition.notify_all()
        if self._connect_requested:
            self._on_reset(True, "insta360_settings_changed")
            self._emit_insta360_status()

    def connect_insta360(self) -> None:
        with self._condition:
            changed = (
                self._source_type != "insta360" or not self._connect_requested
            )
            if changed:
                self._generation += 1
                self._source_session_id = uuid.uuid4().hex
            self._source_type = "insta360"
            self._connect_requested = True
            self._video_id = None
            self._video_path = None
            self._insta360_status = "connecting"
            self._insta360_error = None
            self._last_frame_received_at = None
            self._video_error = None
            self._condition.notify_all()
        if changed:
            self._on_reset(True, "source_changed")
            self._on_event({"type": "source_changed", "source_type": "insta360"})
        self._emit_insta360_status()

    def disconnect_insta360(self) -> None:
        with self._condition:
            self._generation += 1
            self._connect_requested = False
            self._source_session_id = uuid.uuid4().hex
            self._insta360_status = "disconnected"
            self._insta360_error = None
            self._last_frame_received_at = None
            self._condition.notify_all()
        self._on_reset(True, "insta360_disconnected")
        self._emit_insta360_status()

    def select_empty_video(self, loop_enabled: bool) -> int:
        with self._condition:
            self._generation += 1
            generation = self._generation
            self._source_type = "video"
            self._connect_requested = False
            self._source_session_id = uuid.uuid4().hex
            self._video_id = None
            self._video_path = None
            self._loop_enabled = loop_enabled
            self._playback_state = "ready"
            self._position_ms = 0
            self._video_error = None
            self._condition.notify_all()
        self._on_reset(True, "source_changed")
        self._on_event({"type": "source_changed", "source_type": "video"})
        self._emit_video_status()
        return generation

    def activate_video(
        self,
        video_id: int,
        path: Path,
        loop_enabled: bool,
        fps: float | None,
        duration_ms: int | None,
    ) -> None:
        with self._condition:
            self._generation += 1
            self._source_type = "video"
            self._connect_requested = False
            self._source_session_id = uuid.uuid4().hex
            self._video_id = video_id
            self._video_path = path
            self._video_fps = fps
            self._video_duration_ms = duration_ms
            self._loop_enabled = loop_enabled
            self._playback_state = "ready"
            self._position_ms = 0
            self._video_error = None
            self._condition.notify_all()
        self._on_reset(True, "source_changed")
        self._on_event(
            {"type": "source_changed", "source_type": "video", "video_id": video_id}
        )
        self._emit_video_status()

    def set_loop(self, enabled: bool) -> None:
        with self._condition:
            self._loop_enabled = enabled
            self._condition.notify_all()
        self._emit_video_status()

    def playback(self, action: str) -> None:
        reset_cooldown = False
        reset_frame = False
        with self._condition:
            if self._source_type != "video" or self._video_id is None:
                raise RuntimeError("再生する動画が選択されていません")
            previous_state = self._playback_state
            if action == "play":
                if previous_state == "ended":
                    self._generation += 1
                    self._position_ms = 0
                    reset_cooldown = True
                    reset_frame = True
                elif previous_state == "error":
                    raise RuntimeError("エラー状態の動画は先頭から再生してください")
                elif previous_state == "paused":
                    reset_cooldown = False
                self._playback_state = "playing"
                self._video_error = None
            elif action == "pause":
                if previous_state == "playing":
                    self._playback_state = "paused"
            elif action == "restart":
                self._generation += 1
                self._position_ms = 0
                self._playback_state = "playing"
                self._video_error = None
                reset_cooldown = True
                reset_frame = True
            elif action == "stop":
                self._generation += 1
                self._position_ms = 0
                self._playback_state = "ready"
                self._video_error = None
                reset_cooldown = True
                reset_frame = True
            self._condition.notify_all()
        self._on_reset(reset_cooldown, f"video_{action}")
        self._emit_video_status(reset_frame=reset_frame)

    def clear_video(self, video_id: int, loop_enabled: bool) -> None:
        with self._condition:
            if self._video_id != video_id:
                return
        generation = self.select_empty_video(loop_enabled)
        with self._condition:
            released = self._condition.wait_for(
                lambda: self._applied_generation >= generation or self._stop,
                timeout=5,
            )
        if not released:
            raise RuntimeError("動画ファイルを解放できませんでした")

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "source_type": self._source_type,
                "source_session_id": self._source_session_id,
                "camera_ip": self._camera_ip,
                "tcp_port": self._tcp_port,
                "reconnect_interval_ms": self._reconnect_interval_ms,
                "insta360_status": self._insta360_status,
                "insta360_error": self._insta360_error,
                "last_frame_received_at": self._last_frame_received_at,
                "video_id": self._video_id,
                "video_status": self._playback_state,
                "video_error": self._video_error,
                "position_ms": self._position_ms,
                "duration_ms": self._video_duration_ms,
                "fps": self._video_fps,
                "loop_enabled": self._loop_enabled,
            }

    def _emit_insta360_status(self) -> None:
        state = self.snapshot()
        self._on_event(
            {
                "type": "insta360_status",
                "status": state["insta360_status"],
                "error": state["insta360_error"],
                "last_frame_received_at": state["last_frame_received_at"],
            }
        )

    def _emit_video_status(self, looped: bool = False, reset_frame: bool = False) -> None:
        state = self.snapshot()
        self._on_event(
            {
                "type": "video_status",
                "video_id": state["video_id"],
                "status": state["video_status"],
                "position_ms": state["position_ms"],
                "duration_ms": state["duration_ms"],
                "loop": state["loop_enabled"],
                "looped": looped,
                "reset_frame": reset_frame,
                "error": state["video_error"],
            }
        )

    def _set_insta360_status(
        self, status: str, error: str | None = None, generation: int | None = None
    ) -> bool:
        with self._condition:
            if generation is not None and generation != self._generation:
                return False
            changed = status != self._insta360_status or error != self._insta360_error
            self._insta360_status = status
            self._insta360_error = error
        if changed:
            self._emit_insta360_status()
        return True

    def _set_video_status(
        self, status: str, error: str | None = None, generation: int | None = None
    ) -> bool:
        with self._condition:
            if generation is not None and generation != self._generation:
                return False
            changed = status != self._playback_state or error != self._video_error
            self._playback_state = status
            self._video_error = error
        if changed:
            self._emit_video_status()
        return True

    def _next_frame_id(self) -> int:
        with self._condition:
            self._frame_id += 1
            return self._frame_id

    def _is_generation_current(self, generation: int, require_playing: bool = False) -> bool:
        with self._condition:
            return self._generation == generation and (
                not require_playing or self._playback_state == "playing"
            )

    def _offer_current(
        self, frame: FrameInput, generation: int, require_playing: bool = False
    ) -> bool:
        with self._condition:
            if self._generation != generation or (
                require_playing and self._playback_state != "playing"
            ):
                return False
            if frame.source_type == "insta360":
                self._last_frame_received_at = frame.timestamp
            self._on_frame(frame)
            return True

    def _wait(self, seconds: float) -> None:
        with self._condition:
            self._condition.wait(timeout=max(0.01, seconds))

    def _run(self) -> None:
        capture = None
        insta360_source: Insta360Source | None = None
        generation = -1
        frame_number = 0
        next_video_frame_at: float | None = None

        while True:
            with self._condition:
                if self._stop:
                    break
                state = {
                    "generation": self._generation,
                    "source_type": self._source_type,
                    "source_session_id": self._source_session_id,
                    "camera_ip": self._camera_ip,
                    "tcp_port": self._tcp_port,
                    "reconnect_interval_ms": self._reconnect_interval_ms,
                    "connect_requested": self._connect_requested,
                    "video_id": self._video_id,
                    "video_path": self._video_path,
                    "video_fps": self._video_fps,
                    "duration_ms": self._video_duration_ms,
                    "loop": self._loop_enabled,
                    "playback_state": self._playback_state,
                }

            if state["generation"] != generation:
                if capture is not None:
                    capture.release()
                    capture = None
                if insta360_source is not None:
                    insta360_source.close()
                    insta360_source = None
                generation = state["generation"]
                frame_number = 0
                next_video_frame_at = None
                if state["source_type"] == "video" and state["video_path"] is not None:
                    capture = cv2.VideoCapture(str(state["video_path"]))
                    if not capture.isOpened():
                        capture.release()
                        capture = None
                        self._set_video_status(
                            "error", "動画を開けません", generation=generation
                        )
                    elif not self._is_generation_current(generation):
                        capture.release()
                        capture = None
                    else:
                        self._set_video_status("ready", generation=generation)
                with self._condition:
                    self._applied_generation = max(self._applied_generation, generation)
                    self._condition.notify_all()

            if state["source_type"] == "insta360":
                if not state["connect_requested"]:
                    self._wait(0.2)
                    continue
                if insta360_source is None:
                    self._set_insta360_status("connecting", generation=generation)
                    try:
                        insta360_source = Insta360Source(
                            state["camera_ip"],
                            state["tcp_port"],
                        )
                    except Insta360SourceError as exc:
                        if self._is_generation_current(generation):
                            self._on_reset(False, "insta360_disconnected")
                            self._set_insta360_status(
                                "error", str(exc), generation=generation
                            )
                        self._wait(state["reconnect_interval_ms"] / 1000)
                        continue
                    if not self._is_generation_current(generation):
                        insta360_source.close()
                        insta360_source = None
                        continue
                    self._set_insta360_status("connected", generation=generation)

                try:
                    image = insta360_source.read_frame(timeout=0.1)
                except Insta360SourceError as exc:
                    if not self._is_generation_current(generation):
                        insta360_source.close()
                        insta360_source = None
                        continue
                    insta360_source.close()
                    insta360_source = None
                    self._on_reset(False, "insta360_disconnected")
                    self._set_insta360_status("error", str(exc), generation)
                    self._wait(state["reconnect_interval_ms"] / 1000)
                    continue
                if image is None:
                    continue
                self._offer_current(
                    FrameInput(
                        frame_id=self._next_frame_id(),
                        source_session_id=state["source_session_id"],
                        image=image,
                        source_type="insta360",
                        timestamp=datetime.now().astimezone().isoformat(),
                        captured_at=time.monotonic(),
                    ),
                    generation,
                )
                continue

            if state["video_id"] is None or state["video_path"] is None or capture is None:
                self._wait(0.2)
                continue
            if state["playback_state"] != "playing":
                next_video_frame_at = None
                self._wait(0.1)
                continue

            try:
                success, image = capture.read()
            except Exception:
                logger.exception("Could not decode a video frame")
                success, image = False, None
            if not success or image is None:
                if not self._is_generation_current(generation, require_playing=True):
                    continue
                position = capture.get(cv2.CAP_PROP_POS_MSEC)
                duration = state["duration_ms"]
                video_fps = state["video_fps"] or capture.get(cv2.CAP_PROP_FPS) or 25.0
                with self._condition:
                    last_position = self._position_ms
                if not math.isfinite(position) or position <= 0:
                    position = max(last_position, frame_number * 1000 / max(0.1, video_fps))
                end_tolerance = max(100.0, 2000 / max(0.1, video_fps))
                reached_end = (
                    duration is None
                    or not math.isfinite(position)
                    or position >= duration - end_tolerance
                )
                if not reached_end:
                    capture.release()
                    capture = None
                    if self._set_video_status(
                        "error",
                        "動画のデコード中にエラーが発生しました",
                        generation,
                    ):
                        self._on_reset(False, "video_error")
                    continue
                if state["loop"]:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    with self._condition:
                        if self._generation == generation:
                            self._position_ms = 0
                    self._on_reset(True, "video_loop")
                    self._emit_video_status(looped=True, reset_frame=True)
                    next_video_frame_at = None
                    frame_number = 0
                    continue
                capture.release()
                capture = None
                if not self._set_video_status("ended", generation=generation):
                    continue
                with self._condition:
                    if self._generation == generation:
                        self._position_ms = state["duration_ms"] or self._position_ms
                self._on_reset(False, "video_ended")
                self._emit_video_status()
                self._wait(0.2)
                continue

            frame_number += 1
            video_fps = state["video_fps"]
            if video_fps is None or not math.isfinite(video_fps) or video_fps <= 0:
                capture_fps = capture.get(cv2.CAP_PROP_FPS)
                video_fps = capture_fps if math.isfinite(capture_fps) and capture_fps > 0 else 25.0
            position = capture.get(cv2.CAP_PROP_POS_MSEC)
            if not math.isfinite(position) or position <= 0:
                position = (frame_number - 1) * 1000 / video_fps
            position_ms = max(0, int(position))
            with self._condition:
                if self._generation == generation:
                    self._position_ms = position_ms
            if not self._is_generation_current(generation, require_playing=True):
                continue

            self._offer_current(
                FrameInput(
                    frame_id=self._next_frame_id(),
                    source_session_id=state["source_session_id"],
                    image=image,
                    source_type="video",
                    timestamp=datetime.now().astimezone().isoformat(),
                    captured_at=time.monotonic(),
                    video_id=state["video_id"],
                    media_time_ms=position_ms,
                ),
                generation,
                require_playing=True,
            )
            interval = 1 / max(0.1, video_fps)
            next_video_frame_at = (
                next_video_frame_at + interval
                if next_video_frame_at is not None
                else time.monotonic() + interval
            )
            delay = next_video_frame_at - time.monotonic()
            if delay > 0:
                self._wait(delay)
            else:
                next_video_frame_at = time.monotonic()

        if capture is not None:
            capture.release()
        if insta360_source is not None:
            insta360_source.close()


class InferenceWorker:
    def __init__(self, rule_engine: RuleEngine, hub: WebSocketHub):
        self._rule_engine = rule_engine
        self._hub = hub
        self._frames: queue.Queue[tuple[FrameInput, int]] = queue.Queue(maxsize=1)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._pipeline_generation = 0
        self._detector: Detector | None = None
        self._model_id: int | None = None
        self._model_name: str | None = None
        self._model_generation = 0
        self._settings = {
            "confidence": 0.4,
            "iou_threshold": 0.7,
            "imgsz": 416,
            "target_fps": 5.0,
            "jpeg_quality": 80,
        }
        self._last_result_at: float | None = None
        self._stream_stale = False
        self._last_inference_error: str | None = None
        self._inference_times: deque[float] = deque(maxlen=30)
        self._thread = threading.Thread(target=self._run, name="cpu-inference", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def offer(self, frame: FrameInput) -> None:
        with self._lock:
            item = (frame, self._pipeline_generation)
            try:
                self._frames.put_nowait(item)
            except queue.Full:
                try:
                    self._frames.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._frames.put_nowait(item)
                except queue.Full:
                    logger.warning("Dropping a captured frame because the inference queue is full")

    def clear_frames(self) -> None:
        with self._lock:
            self._pipeline_generation += 1
            self._last_result_at = None
            self._stream_stale = False
            self._inference_times.clear()
            try:
                while True:
                    self._frames.get_nowait()
            except queue.Empty:
                return

    def set_model(self, detector: Detector | None, model_id: int | None, name: str | None) -> None:
        with self._lock:
            self._detector = detector
            self._model_id = model_id
            self._model_name = name
            self._model_generation += 1
            generation = self._model_generation
            self._last_result_at = None
            self._stream_stale = False
        self.clear_frames()
        if detector is None:
            class_names: set[str] = set()
        else:
            class_names = set(detector.names.values())
        cleared = self._rule_engine.set_model_classes(class_names)
        for event in cleared:
            self._hub.publish_event(event)
        self._hub.publish_event(
            {
                "type": "model_changed",
                "model_id": model_id,
                "model_name": name,
                "model_generation": generation,
            }
        )

    def set_settings(self, settings: dict[str, Any]) -> None:
        with self._lock:
            self._settings.update(settings)

    def _run(self) -> None:
        next_inference_at = 0.0
        while not self._stop.is_set():
            with self._lock:
                detector = self._detector
                settings = dict(self._settings)
                generation = self._model_generation
            if detector is None:
                self._check_stale()
                self._stop.wait(0.2)
                continue

            delay = next_inference_at - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
                if self._stop.is_set():
                    break
            try:
                frame, frame_generation = self._frames.get(timeout=0.2)
            except queue.Empty:
                self._check_stale()
                continue

            with self._lock:
                if (
                    frame_generation != self._pipeline_generation
                    or generation != self._model_generation
                ):
                    continue
            started_at = time.monotonic()
            try:
                detections = detector.predict(
                    frame.image,
                    confidence=settings["confidence"],
                    iou_threshold=settings["iou_threshold"],
                    imgsz=settings["imgsz"],
                )
                processing_ms = (time.monotonic() - started_at) * 1000
                finished_at = time.monotonic()
                with self._lock:
                    if (
                        generation != self._model_generation
                        or frame_generation != self._pipeline_generation
                    ):
                        continue
                    self._last_result_at = finished_at
                    self._stream_stale = False
                    self._inference_times.append(finished_at)
                    current_model_id = self._model_id
                    current_model_name = self._model_name
                if self._last_inference_error is not None:
                    self._last_inference_error = None
                    self._hub.publish_event(
                        {"type": "error", "code": "inference_recovered", "cleared": True}
                    )
                matched_rule_ids, rule_events = self._rule_engine.evaluate(
                    detections=detections,
                    frame_time=(
                        frame.media_time_ms / 1000
                        if frame.source_type == "video" and frame.media_time_ms is not None
                        else frame.captured_at
                    ),
                    source_type=frame.source_type,
                    video_id=frame.video_id,
                    media_time_ms=frame.media_time_ms,
                    model_id=current_model_id,
                )

                success, encoded = cv2.imencode(
                    ".jpg",
                    frame.image,
                    [
                        int(cv2.IMWRITE_JPEG_QUALITY),
                        int(settings.get("jpeg_quality", 80)),
                    ],
                )
                if not success:
                    raise RuntimeError("推論フレームを JPEG に変換できません")
                height, width = frame.image.shape[:2]
                fps = self._current_fps()
                header = {
                    "type": "frame",
                    "frame_id": frame.frame_id,
                    "source_session_id": frame.source_session_id,
                    "timestamp": frame.timestamp,
                    "source_type": frame.source_type,
                    "video_id": frame.video_id,
                    "media_time_ms": frame.media_time_ms,
                    "width": width,
                    "height": height,
                    "detections": [detection.as_dict() for detection in detections],
                    "matched_rule_ids": matched_rule_ids,
                    "processing_ms": round(processing_ms, 2),
                    "inference_fps": round(fps, 2),
                    "model_name": current_model_name,
                }
                header_bytes = json.dumps(
                    header, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                packet = struct.pack("<I", len(header_bytes)) + header_bytes + encoded.tobytes()
                alert_events = [
                    event
                    for event in rule_events
                    if event["type"] == "alert_started" and not event.get("test")
                ]
                alert_events_by_rule = {}
                alert_save_error = None
                if alert_events:
                    try:
                        with SessionLocal.begin() as db:
                            persisted_events = persist_alert_frame_events(
                                db,
                                events=alert_events,
                                source_session_id=frame.source_session_id,
                                frame_id=frame.frame_id,
                                source_type=frame.source_type,
                                timestamp=frame.timestamp,
                                video_id=frame.video_id,
                                media_time_ms=frame.media_time_ms,
                                model_id=current_model_id,
                                model_name=current_model_name,
                                width=width,
                                height=height,
                                image_jpeg=encoded.tobytes(),
                                detections=detections,
                            )
                        alert_events_by_rule = {
                            event["rule_id"]: event for event in persisted_events
                        }
                    except Exception as exc:
                        logger.exception("Could not save alert frame")
                        alert_save_error = {
                            "type": "alert_save_error",
                            "message": f"アラート履歴を保存できませんでした: {exc}",
                            "frame_id": frame.frame_id,
                            "source_session_id": frame.source_session_id,
                        }
                        alert_events_by_rule = {
                            event["rule_id"]: {
                                **event,
                                "frame_id": frame.frame_id,
                                "source_session_id": frame.source_session_id,
                            }
                            for event in alert_events
                        }
                with self._lock:
                    if (
                        generation != self._model_generation
                        or frame_generation != self._pipeline_generation
                    ):
                        continue
                    for event in rule_events:
                        if event["type"] == "alert_started" and not event.get("test"):
                            event = alert_events_by_rule.get(event["rule_id"], event)
                        self._hub.publish_event(event)
                    if alert_save_error is not None:
                        self._hub.publish_event(alert_save_error)
                    self._hub.publish_frame(packet)
                    self._last_inference_error = None
                next_inference_at = finished_at + 1 / max(0.1, settings["target_fps"])
            except Exception as exc:
                message = str(exc)
                logger.exception("Inference failed")
                if message != self._last_inference_error:
                    self._last_inference_error = message
                    self._hub.publish_event(
                        {
                            "type": "error",
                            "code": "inference_failed",
                            "message": f"推論に失敗しました: {message}",
                        }
                    )
                next_inference_at = time.monotonic() + 1 / max(
                    0.1, settings["target_fps"]
                )

    def _current_fps(self) -> float:
        if len(self._inference_times) < 2:
            return 0.0
        elapsed = self._inference_times[-1] - self._inference_times[0]
        return (len(self._inference_times) - 1) / elapsed if elapsed > 0 else 0.0

    def _check_stale(self) -> None:
        with self._lock:
            last_result = self._last_result_at
            if (
                self._detector is None
                or last_result is None
                or self._stream_stale
                or time.monotonic() - last_result < 2
            ):
                return
            self._stream_stale = True
        events = self._rule_engine.reset(clear_cooldown=False, reason="stream_stale")
        for event in events:
            self._hub.publish_event(event)
        self._hub.publish_event(
            {
                "type": "error",
                "code": "stream_stale",
                "message": "推論結果が2秒以上届いていません",
            }
        )

class LiveRuntime:
    def __init__(self, hub: WebSocketHub):
        self.hub = hub
        self.rules = RuleEngine()
        self.inference = InferenceWorker(self.rules, hub)
        self.capture = CaptureController(
            on_frame=self.inference.offer,
            on_event=self.hub.publish_event,
            on_reset=self._reset_pipeline,
        )
        self.active_model_id: int | None = None
        self.active_model_name: str | None = None
        self.model_error: str | None = None
        self.model_loaded = False

    async def start(self) -> None:
        with SessionLocal() as db:
            model_settings = db.get(ModelSettings, 1)
            source_settings = db.get(SourceSettings, 1)
            insta360_settings = db.get(Insta360Settings, 1)
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            rule_records = list(
                db.scalars(
                    select(AlertRule).options(selectinload(AlertRule.classes))
                ).all()
            )
            if model_settings is not None:
                jpeg_quality_setting = db.get(AppSetting, "jpeg_quality")
                self.inference.set_settings(
                    {
                        "confidence": model_settings.confidence,
                        "iou_threshold": model_settings.iou_threshold,
                        "imgsz": model_settings.imgsz,
                        "target_fps": model_settings.target_fps,
                        "jpeg_quality": int(
                            json.loads(jpeg_quality_setting.value_json)
                            if jpeg_quality_setting is not None
                            else 80
                        ),
                    }
                )
                if model_settings.active_model_id is not None:
                    active_model = db.get(ModelRecord, model_settings.active_model_id)
                    if active_model is not None:
                        self.active_model_id = active_model.id
                        self.active_model_name = active_model.name
                        model_path = Path(active_model.stored_filename)
                        if not model_path.is_absolute():
                            model_path = MODELS_DIR / model_path
                    else:
                        model_path = None
                else:
                    active_model = None
                    model_path = None
            else:
                active_model = None
                model_path = None

            self.rules.configure(
                [
                    {
                        "id": rule.id,
                        "name": rule.name,
                        "class_names": [item.class_name for item in rule.classes],
                        "enabled": rule.enabled,
                        "detection_duration_ms": rule.detection_duration_ms,
                        "release_duration_ms": rule.release_duration_ms,
                        "cooldown_ms": rule.cooldown_ms,
                    }
                    for rule in rule_records
                ]
            )
            if insta360_settings is not None:
                self.capture.configure_insta360(
                    insta360_settings.camera_ip,
                    insta360_settings.tcp_port,
                    insta360_settings.reconnect_interval_ms,
                )
            source_snapshot = (
                {
                    "source_type": source_settings.source_type,
                    "active_video_id": source_settings.active_video_id,
                    "loop_enabled": source_settings.loop_enabled,
                }
                if source_settings is not None
                else None
            )
            if source_snapshot and source_snapshot["source_type"] == "video":
                video_id = source_snapshot["active_video_id"]
                if video_id is not None:
                    video = db.get(Video, video_id)
                    if video is not None:
                        self.capture.activate_video(
                            video.id,
                            VIDEOS_DIR / video.stored_filename,
                            source_snapshot["loop_enabled"],
                            video.fps,
                            video.duration_ms,
                        )
                    else:
                        self.capture.select_empty_video(source_snapshot["loop_enabled"])
                else:
                    self.capture.select_empty_video(source_snapshot["loop_enabled"])

        if active_model is not None and model_path is not None:
            try:
                detector = await asyncio.to_thread(Detector.load, model_path)
                self.inference.set_model(detector, active_model.id, active_model.name)
                self.model_loaded = True
            except Exception as exc:
                self.model_error = f"有効モデルを読み込めません: {exc}"
                self.model_loaded = False
                logger.exception("Could not restore the active model")
                self.inference.set_model(None, active_model.id, active_model.name)
                self.hub.publish_event(
                    {
                        "type": "error",
                        "code": "active_model_load_failed",
                        "message": self.model_error,
                    }
                )
        self.capture.start()
        self.inference.start()

    def stop(self) -> None:
        self.capture.stop()
        self.inference.stop()

    def _reset_pipeline(self, clear_cooldown: bool, reason: str) -> None:
        self.inference.clear_frames()
        for event in self.rules.reset(clear_cooldown, reason):
            self.hub.publish_event(event)

    def configure_rules(self, rules: list[dict[str, Any]]) -> None:
        for event in self.rules.configure(rules):
            self.hub.publish_event(event)
        self.hub.publish_event({"type": "rules_changed"})

    def set_model(self, detector: Detector, model_id: int, name: str) -> None:
        self.model_error = None
        self.model_loaded = True
        self.active_model_id = model_id
        self.active_model_name = name
        self.inference.set_model(detector, model_id, name)

    def set_inference_settings(self, settings: dict[str, Any]) -> None:
        self.inference.set_settings(settings)

    def source_snapshot(self) -> dict[str, Any]:
        return self.capture.snapshot()

    def test_alert(self) -> None:
        self.hub.publish_event(
            {
                "type": "alert_started",
                "test": True,
                "rule_id": None,
                "rule_name": "テスト通知",
                "detected_classes": [],
                "timestamp": datetime.now().astimezone().isoformat(),
                "source_type": self.capture.snapshot()["source_type"],
                "video_id": None,
                "media_time_ms": None,
            }
        )
