"""Make moved flowgate test providers importable to legacy test consumers."""
from pathlib import Path
import sys

_PROVIDER_DIR = Path(__file__).resolve().parent / "flowgate"
if str(_PROVIDER_DIR) not in sys.path:
    sys.path.insert(0, str(_PROVIDER_DIR))