import sys
from pathlib import Path

# Make backend/ importable so tests can `import kb_browser` regardless of
# how pytest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
