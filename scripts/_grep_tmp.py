import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = Path(__file__).resolve().parent.parent
pattern = re.compile(sys.argv[1])
targets = []
for folder in ("event_sync", "tests", "scripts", ".github/workflows", "docs"):
    targets.extend((root / folder).rglob("*.*"))
targets.extend(root.glob("*.py"))
targets.extend(root.glob("*.md"))
targets.extend([root / ".env", root / ".env.example"])

for path in sorted(set(targets)):
    if path.suffix in {".pyc", ".png", ".avif", ".csv"} or "__pycache__" in str(path):
        continue
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, FileNotFoundError, PermissionError):
        continue
    for i, line in enumerate(lines, 1):
        if pattern.search(line):
            rel = path.relative_to(root)
            print(f"{rel}:{i}: {line.rstrip()[:140]}")
