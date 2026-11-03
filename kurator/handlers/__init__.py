"""File-type handlers.

A handler knows how to turn one kind of file into:
  * a PIL image to make thumbnails from (for images, Krita files, later .blend previews...)
  * a dict of metadata worth keeping/showing/searching
  * embedded creation dates  ({'exif-original': ts, 'xmp': ts, ...})

Adding support for a new format = adding a module here and registering it.
Audio handlers return a waveform, 3D handlers a rendered preview (see kurator/mesh3d).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PIL import Image


@dataclass
class Loaded:
    image: Image.Image | None
    meta: dict = field(default_factory=dict)
    dates: dict = field(default_factory=dict)
    size: tuple[int, int] | None = None   # original pixel size (before any draft scaling)
    extra: dict = field(default_factory=dict)   # extra pictures stored with the thumbnails, e.g. {1: hover strip}


# Groups for the type filter, in display order.
GROUPS = [
    ("image", "Images"),
    ("work", "Work files (layered)"),
    ("vector", "Vector"),
    ("document", "Documents"),
    ("texture", "Game textures"),
    ("3d", "3D models"),
    ("video", "Video"),
    ("audio", "Audio"),
]


@dataclass(frozen=True)
class Handler:
    kind: str                       # "image", "audio", "model" or "video" (what the thumbnail shows / how it opens)
    extensions: tuple[str, ...]     # lower-case, with dot
    load: Callable[[bytes, str, int | None], Loaded]  # (data, path, thumb_target_px) -> Loaded
    browser_viewable: bool = False  # can the browser show / play the original file directly?
    group: str = "image"            # type-filter group, see GROUPS
    streams: bool = False           # big files (video): load() gets data=None and reads the path itself


_REGISTRY: dict[str, Handler] = {}


def register(handler: Handler) -> None:
    for ext in handler.extensions:
        _REGISTRY[ext] = handler


def handler_for(ext: str) -> Handler | None:
    return _REGISTRY.get(ext.lower())


def supported_extensions() -> list[str]:
    return sorted(_REGISTRY)


def supported_types() -> dict:
    """For the type filter: groups with their extensions."""
    by_group: dict[str, list[str]] = {}
    for ext, h in sorted(_REGISTRY.items()):
        by_group.setdefault(h.group, []).append(ext.lstrip("."))
    return {"groups": [{"key": k, "label": label, "exts": by_group.get(k, [])} for k, label in GROUPS if by_group.get(k)],
            "viewable": sorted(e.lstrip(".") for e, h in _REGISTRY.items() if h.browser_viewable),
            "audio": sorted(e.lstrip(".") for e, h in _REGISTRY.items() if h.kind == "audio"),
            "models": sorted(e.lstrip(".") for e, h in _REGISTRY.items() if h.kind == "model"),
            "video": sorted(e.lstrip(".") for e, h in _REGISTRY.items() if h.kind == "video"),
            "meshes": sorted(e.lstrip(".") for e, h in _REGISTRY.items() if h.load.__name__ == "load_mesh_file")}


# Built-in handlers register themselves on import.
from . import image as _image  # noqa: E402,F401
from . import krita as _krita  # noqa: E402,F401
from . import artapps as _artapps  # noqa: E402,F401
from . import vector as _vector  # noqa: E402,F401
from . import audio as _audio  # noqa: E402,F401
from . import model3d as _model3d  # noqa: E402,F401
from . import video as _video  # noqa: E402,F401
from . import placeholder as _placeholder  # noqa: E402,F401
