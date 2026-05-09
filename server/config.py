from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


class TimingConfig(BaseModel):
    room_intro: int = Field(default=4, ge=1, le=30)
    countdown_per_shot: int = Field(default=5, ge=1, le=30)
    interval_between_shots: int = Field(default=2, ge=0, le=10)
    transit_time: int = Field(default=45, ge=5, le=180)
    shots_per_room: int = Field(default=3, ge=1, le=5)


class RoomConfig(BaseModel):
    name: str
    camera_ip: str
    tablet_id: str
    lens: str
    apply_barrel_distortion: bool = False
    simulated_capture: bool = False


class PrinterConfig(BaseModel):
    name: str = "DNP DS-RX1HS"
    paper_size: Literal["4x6", "2x6"] = "4x6"


class NetworkConfig(BaseModel):
    server_host: str = "0.0.0.0"
    server_port: int = Field(default=8000, ge=1, le=65535)
    websocket_path: str = "/ws"


class RetentionConfig(BaseModel):
    keep_days: int = Field(default=7, ge=1, le=365)
    purge_time_utc: str = "03:00"


class AppConfig(BaseModel):
    profile: Literal["dev_single_cam", "staging_multi_cam", "prod"] = "dev_single_cam"
    timing: TimingConfig = Field(default_factory=TimingConfig)
    rooms: dict[str, RoomConfig] = Field(default_factory=dict)
    printer: PrinterConfig = Field(default_factory=PrinterConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(config_path.read_text()) or {}
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid config file at {config_path}: {exc}") from exc
