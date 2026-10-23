"""Writable per-user storage and read-only resources for source and frozen builds."""
import os,sys
from pathlib import Path

APP_DIR=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
RESOURCE_DIR=Path(__file__).resolve().parent
DATA=Path(os.environ.get('PARAKEET_DATA_DIR',str(Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'ParakeetNPUSubtitles')))
MODEL_ROOT=Path(os.environ.get('PARAKEET_MODEL_ROOT',str(DATA/'models')))
VERSION='0.1.2'
