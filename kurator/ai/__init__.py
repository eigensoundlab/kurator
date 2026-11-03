"""Optional AI features (install with: install.bat ai / ./install.sh ai).

The core app never imports torch: everything here loads lazily, so Kurator runs fine without it.
"""

from .service import AIError, AIService  # noqa: F401
