"""Shared test settings."""

import os
import sys

# helpers next to the tests (model_samples, textured_samples...) import each other
sys.path.insert(0, os.path.dirname(__file__))
# Turntable strips (12 renders per model) only where a test asks for them: keeps the suite quick.
os.environ.setdefault("KURATOR_TURNTABLE", "0")
