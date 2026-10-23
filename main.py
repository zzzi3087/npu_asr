"""Source checkout entry point. Windows packages use the frozen entry point."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from app import main

if __name__=='__main__':sys.exit(main())
