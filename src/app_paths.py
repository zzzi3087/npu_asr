"""Writable per-user storage and read-only resources for source and frozen builds."""
import os,sys
from pathlib import Path

FROZEN=bool(getattr(sys,'frozen',False))
APP_DIR=Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
RESOURCE_DIR=Path(__file__).resolve().parent if FROZEN else APP_DIR/'resources'
DATA=Path(os.environ.get('PARAKEET_DATA_DIR',str(Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'ParakeetNPUSubtitles')))
MODEL_ROOT=Path(os.environ.get('PARAKEET_MODEL_ROOT',str(DATA/'models')))
VERSION='3.0'
