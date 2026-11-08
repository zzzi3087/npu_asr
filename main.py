"""从源码启动实时字幕。Windows 安装包使用打包时指定的入口。"""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from app import main

if __name__=='__main__':sys.exit(main())
