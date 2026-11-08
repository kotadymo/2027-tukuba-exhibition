from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SourcePatch(BaseModel):
    source_type: Literal["insta360", "video"]

class Insta360SettingsPatch(BaseModel):
    camera_ip: str | None = Field(default=None, min_length=1, max_length=255)
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    reconnect_interval_ms: int | None = Field(default=None, ge=500, le=60000)

    @field_validator("camera_ip")
    @classmethod
    def validate_camera_ip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        import ipaddress

        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError("有効な IP アドレスを入力してください") from exc
        if address.version != 4:
            raise ValueError("GO Ultra の接続先には IPv4 アドレスを指定してください")
        return str(address)


class PlaybackAction(BaseModel):
    action: Literal["play", "pause", "restart", "stop"]


class LoopPatch(BaseModel):
    loop: bool


class RulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    class_names: list[str] = Field(min_length=2, max_length=64)
    enabled: bool = True
    detection_duration_ms: int = Field(default=500, ge=0, le=600_000)
    release_duration_ms: int = Field(default=2000, ge=0, le=600_000)
    cooldown_ms: int = Field(default=10_000, ge=0, le=3_600_000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ルール名を入力してください")
        return value

    @field_validator("class_names")
    @classmethod
    def validate_classes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("クラス名は空にできません")
        if len(set(normalized)) != len(normalized):
            raise ValueError("同じクラスを重複して選択できません")
        if len(normalized) < 2:
            raise ValueError("ルールには異なるクラスを2種類以上選択してください")
        return normalized


class SettingsPatch(BaseModel):
    confidence: float | None = Field(default=None, ge=0, le=1)
    iou_threshold: float | None = Field(default=None, ge=0, le=1)
    imgsz: int | None = Field(default=None, ge=32, le=2048)
    target_fps: float | None = Field(default=None, gt=0, le=60)
    jpeg_quality: int | None = Field(default=None, ge=1, le=100)
    show_boxes: bool | None = None
    show_labels: bool | None = None
    show_confidence: bool | None = None
    audio_enabled: bool | None = None
