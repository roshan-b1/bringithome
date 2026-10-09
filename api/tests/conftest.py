import os
import sys
from pathlib import Path

os.environ["BACKTRACK_API_NO_DEFAULT_APP"] = "1"   # tests build their own app
os.environ["BACKTRACK_ENV_FILE"] = os.devnull       # never read the real .env
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker" / "tests"))  # gtfs_fixture
