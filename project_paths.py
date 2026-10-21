"""城堡地基：项目路径。所有数据根据本文件位置保存，不依赖终端当前目录。"""
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / 'output'
