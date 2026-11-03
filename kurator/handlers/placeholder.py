"""Formats Kurator can't render itself. They are still catalogued (tag, move, back up) and get the
thumbnail Windows Explorer shows when the application is installed, or a neutral card otherwise."""

from __future__ import annotations

from . import Handler, Loaded, register
from .common import placeholder, shell_thumbnail, zip_image

# Some of these are ZIP containers with a preview inside (Sketch, Adobe XD, local Figma files).
ZIP_PREVIEWS = ["previews/preview.png", "preview.png", "thumbnail.png", "Thumbnails/thumbnail.png", "renditions/preview.png"]

NO_PREVIEW = {
    ".afdesign": "vector", ".afphoto": "work", ".afpub": "document",   # Affinity
    ".xcf": "work",                                                    # GIMP
    ".sai": "work", ".sai2": "work",                                   # PaintTool SAI
    ".mdp": "work",                                                    # FireAlpaca / MediBang
    ".pdn": "work",                                                    # Paint.NET
    ".sla": "document",                                                # Scribus
    ".fig": "vector", ".xd": "vector", ".sketch": "vector",            # UI design tools
}


def load(data: bytes, path: str, target: int | None) -> Loaded:
    ext = "." + path.rsplit(".", 1)[-1].lower()
    if data[:2] == b"PK":
        img = zip_image(data, names=ZIP_PREVIEWS)
        if img is not None:
            return Loaded(img, {"format": ext[1:].upper(), "preview": "embedded"}, {}, None)
    img = shell_thumbnail(path)
    if img is not None:
        return Loaded(img, {"format": ext[1:].upper(), "preview": "windows"}, {}, None)
    return Loaded(placeholder(ext, "no preview"), {"format": ext[1:].upper(), "placeholder": True}, {}, None)


for _ext, _group in NO_PREVIEW.items():
    register(Handler("image", (_ext,), load, group=_group))
