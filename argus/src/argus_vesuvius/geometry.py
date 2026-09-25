from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, order=True)
class Region3D:
    """A half-open region in voxel coordinates: [z0:z1, y0:y1, x0:x1]."""

    volume_id: str
    z0: int
    y0: int
    x0: int
    z1: int
    y1: int
    x1: int

    def __post_init__(self) -> None:
        if not self.volume_id.strip():
            raise ValueError("volume_id must be non-empty")
        if min(self.z0, self.y0, self.x0) < 0:
            raise ValueError("region coordinates must be non-negative")
        if self.z1 <= self.z0 or self.y1 <= self.y0 or self.x1 <= self.x0:
            raise ValueError("region ends must be greater than starts")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Region3D":
        bbox = value.get("bbox_zyx")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 6:
            raise ValueError("bbox_zyx must contain [z0, y0, x0, z1, y1, x1]")
        return cls(str(value.get("volume_id", "")), *(int(v) for v in bbox))

    @classmethod
    def from_bbox(cls, volume_id: str, bbox: Iterable[int]) -> "Region3D":
        values = tuple(int(v) for v in bbox)
        if len(values) != 6:
            raise ValueError("bbox must contain six integers")
        return cls(volume_id, *values)

    @property
    def bbox_zyx(self) -> tuple[int, int, int, int, int, int]:
        return (self.z0, self.y0, self.x0, self.z1, self.y1, self.x1)

    @property
    def voxel_count(self) -> int:
        return (self.z1 - self.z0) * (self.y1 - self.y0) * (self.x1 - self.x0)

    def overlaps(self, other: "Region3D") -> bool:
        if self.volume_id != other.volume_id:
            return False
        return (
            self.z0 < other.z1
            and other.z0 < self.z1
            and self.y0 < other.y1
            and other.y0 < self.y1
            and self.x0 < other.x1
            and other.x0 < self.x1
        )

    def to_mapping(self) -> dict[str, Any]:
        return {"volume_id": self.volume_id, "bbox_zyx": list(self.bbox_zyx)}
