#!/usr/bin/env python3
"""Store byte-identical Windows tool names as hard links before tar packaging."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import uuid


def duplicate_groups(root):
    by_size = {}
    bin_dir = root / "bin"
    if not bin_dir.is_dir():
        raise ValueError(f"Windows tool directory not found: {bin_dir}")
    for path in sorted(bin_dir.iterdir()):
        if path.is_symlink() or path.suffix.lower() != ".exe":
            continue
        info = path.stat()
        if stat.S_ISREG(info.st_mode):
            # A hard link shares permissions too. Preserve differing install
            # modes instead of silently changing metadata to save space.
            by_size.setdefault((info.st_size, stat.S_IMODE(info.st_mode)), []).append(path)
    for paths in by_size.values():
        if len(paths) < 2:
            continue
        by_hash = {}
        for path in paths:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            by_hash.setdefault(digest.digest(), []).append(path)
        yield from (group for group in by_hash.values() if len(group) > 1)


def deduplicate(root, check=False):
    count = 0
    saved = 0
    for canonical, *aliases in duplicate_groups(root):
        for alias in aliases:
            if canonical.samefile(alias):
                continue
            if check:
                raise ValueError(f"duplicate tool is not a hard link: {alias.name} -> {canonical.name}")
            temporary = alias.with_name(f".{alias.name}.{uuid.uuid4().hex}.tmp")
            try:
                os.link(canonical, temporary)
                os.replace(temporary, alias)
            finally:
                if temporary.exists():
                    temporary.unlink()
            count += 1
            saved += canonical.stat().st_size
            print(f"Hard-linked {alias.name} -> {canonical.name}")
    return count, saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Staged or extracted esp-clang directory")
    parser.add_argument("--check", action="store_true", help="Require extracted duplicates to share file identity")
    args = parser.parse_args()
    count, saved = deduplicate(args.root, check=args.check)
    print("Windows tool hard links verified" if args.check else
          f"Deduplicated {count} tools; saved {saved / (1024 * 1024):.2f} MiB before compression")
