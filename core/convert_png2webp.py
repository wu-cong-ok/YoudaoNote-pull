#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Obsidian PNG -> WebP Converter v3

Features
--------
- PNG/png -> WebP
- Dry Run
- Skip if WebP is larger
- Supports Chinese filenames
- Supports spaces
- Later parts:
    * WikiLink
    * Markdown Image
    * HTML img
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# ----------------------------------------------------
# Data
# ----------------------------------------------------

@dataclass
class ImageTask:
    png: Path
    webp: Path

    old_size: int = 0
    new_size: int = 0

    converted: bool = False
    skipped: bool = False
    reason: str = ""


# ----------------------------------------------------
# Args
# ----------------------------------------------------

def parse_args():

    parser = argparse.ArgumentParser(
        description="Convert PNG to WebP inside Obsidian Vault."
    )

    parser.add_argument(
        "vault",
        type=Path,
        help="Vault path"
    )

    parser.add_argument(
        "--quality",
        default=90,
        type=int,
        help="WebP quality (default=90)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only"
    )

    parser.add_argument(
        "--lossless",
        action="store_true",
        help="Use lossless WebP"
    )

    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep original PNG files after conversion"
    )

    parser.add_argument(
        "--verbose",
        action="store_true"
    )

    return parser.parse_args()


# ----------------------------------------------------
# Check cwebp
# ----------------------------------------------------

def check_environment():

    if shutil.which("cwebp") is None:
        print("ERROR: cwebp not found.")
        print("Install first:")
        print("    brew install webp")
        sys.exit(1)


# ----------------------------------------------------
# Scan files
# ----------------------------------------------------

EXCLUDED_DIRS = {".git", ".obsidian", "node_modules", ".trash"}


def _is_excluded(path: Path) -> bool:
    return any(p.name in EXCLUDED_DIRS for p in path.parents)


def scan_png(vault: Path):

    tasks = []

    for file in vault.rglob("*"):

        if not file.is_file():
            continue

        if _is_excluded(file):
            continue

        if file.suffix.lower() != ".png":
            continue

        tasks.append(
            ImageTask(
                png=file,
                webp=file.with_suffix(".webp")
            )
        )

    return tasks


def scan_markdown(vault: Path):

    files = []
    for md in vault.rglob("*.md"):
        if not _is_excluded(md):
            files.append(md)
    return files


# ----------------------------------------------------
# Folder Size
# ----------------------------------------------------

def folder_size(path: Path):

    total = 0

    for file in path.rglob("*"):

        if file.is_file():
            try:
                total += file.stat().st_size
            except:
                pass

    return total


# ----------------------------------------------------
# Convert One Image
# ----------------------------------------------------

def convert_one(
    task: ImageTask,
    quality: int,
    lossless: bool,
    dry_run: bool,
    verbose: bool,
):

    task.old_size = task.png.stat().st_size

    if task.webp.exists() and task.png.stat().st_mtime <= task.webp.stat().st_mtime:

        task.skipped = True
        task.reason = "webp up-to-date"

        return

    if dry_run:

        task.converted = True
        return

    cmd = ["cwebp"]

    if lossless:
        cmd.append("-lossless")
    else:
        cmd.extend([
            "-q",
            str(quality)
        ])

    cmd.extend([
        str(task.png),
        "-o",
        str(task.webp)
    ])

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    if result.returncode != 0:

        task.skipped = True
        task.reason = "convert failed"
        return

    if not task.webp.exists():

        task.skipped = True
        task.reason = "output missing"
        return

    task.new_size = task.webp.stat().st_size

    if task.new_size >= task.old_size:

        task.webp.unlink()

        task.skipped = True
        task.reason = "webp larger"

        return

    task.converted = True

    if verbose:

        saved = (task.old_size - task.new_size) / 1024

        print(
            f"✔ {task.png.name}  "
            f"-{saved:.1f} KB"
        )


# ----------------------------------------------------
# Convert All
# ----------------------------------------------------

def convert_all(tasks, args):

    converted = []
    skipped = []

    print(f"Found {len(tasks)} PNG files")

    for task in tasks:

        convert_one(
            task,
            args.quality,
            args.lossless,
            args.dry_run,
            args.verbose
        )

        if task.converted:
            converted.append(task)
        else:
            skipped.append(task)

    print(
        f"Converted: {len(converted)}"
    )

    print(
        f"Skipped: {len(skipped)}"
    )

    return converted, skipped

# ----------------------------------------------------
# Regex
# ----------------------------------------------------

WIKILINK_PATTERN = re.compile(
    r'!\[\[([^\]]+?)\]\]',
    flags=re.IGNORECASE,
)

MARKDOWN_PATTERN = re.compile(
    r'!\[[^\]]*?\]\(([^)]+?)\)',
    flags=re.IGNORECASE,
)

HTML_PATTERN = re.compile(
    r'<img\b[^>]*?src=["\']([^"\']+)["\']',
    flags=re.IGNORECASE,
)

# ----------------------------------------------------
# Build Replacement Map
# ----------------------------------------------------

def build_replace_map(tasks):

    mapping = {}

    for task in tasks:

        if not task.converted:
            continue

        old = task.png.as_posix()
        new = task.webp.as_posix()

        mapping[old] = new
        mapping[task.png.name] = task.webp.name

    return mapping


# ----------------------------------------------------
# Rewrite Helpers
# ----------------------------------------------------

def replace_target(target, mapping):

    if target in mapping:
        return mapping[target]

    name = Path(target).name

    if name in mapping:
        return target[:-len(name)] + mapping[name]

    return target


def rewrite_markdown_file(md, mapping, dry_run=False):

    text = md.read_text(encoding="utf-8")

    changed = 0

    # ---------- WikiLink ----------

    def wiki(match):

        nonlocal changed

        body = match.group(1)

        if "|" in body:
            target, suffix = body.split("|", 1)
            suffix = "|" + suffix
        else:
            target = body
            suffix = ""

        new_target = replace_target(target, mapping)

        if new_target != target:
            changed += 1

        return f"![[{new_target}{suffix}]]"

    text = WIKILINK_PATTERN.sub(wiki, text)

    # ---------- Markdown ----------

    def mdimg(match):

        nonlocal changed

        target = match.group(1)

        new_target = replace_target(target, mapping)

        if new_target != target:
            changed += 1

        return match.group(0).replace(target, new_target)

    text = MARKDOWN_PATTERN.sub(mdimg, text)

    # ---------- HTML ----------

    def html(match):

        nonlocal changed

        target = match.group(1)

        new_target = replace_target(target, mapping)

        if new_target != target:
            changed += 1

        return match.group(0).replace(target, new_target)

    text = HTML_PATTERN.sub(html, text)

    if changed and not dry_run:
        md.write_text(text, encoding="utf-8")

    return changed


def rewrite_all_markdown(vault, converted_tasks, dry_run=False):

    mapping = build_replace_map(converted_tasks)

    changed_files = 0
    changed_links = 0

    for md in scan_markdown(vault):

        count = rewrite_markdown_file(
            md,
            mapping,
            dry_run
        )

        if count:
            changed_files += 1
            changed_links += count

    return changed_files, changed_links


# ----------------------------------------------------
# Delete PNG
# ----------------------------------------------------

def delete_png(tasks, keep=False, dry_run=False):

    if keep or dry_run:
        return 0

    deleted = 0

    for task in tasks:

        if not task.converted:
            continue

        try:
            task.png.unlink()
            deleted += 1
        except Exception:
            pass

    return deleted


# ----------------------------------------------------
# Programmatic API
# ----------------------------------------------------

def run(
    vault: str | Path,
    quality: int = 90,
    lossless: bool = False,
    keep: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:

    check_environment()

    vault = Path(vault).expanduser().resolve()

    if not vault.exists():
        return {"error": "Vault not found."}

    before = folder_size(vault)

    tasks = scan_png(vault)

    class Args:
        pass

    args = Args()
    args.quality = quality
    args.lossless = lossless
    args.keep = keep
    args.dry_run = dry_run
    args.verbose = verbose

    converted, skipped = convert_all(tasks, args)

    changed_files, changed_links = rewrite_all_markdown(
        vault,
        converted,
        dry_run
    )

    deleted = delete_png(
        converted,
        keep=keep,
        dry_run=dry_run
    )

    after = before if dry_run else folder_size(vault)

    result = {
        "png_found": len(tasks),
        "converted": len(converted),
        "skipped": len(skipped),
        "markdown_modified": changed_files,
        "links_updated": changed_links,
        "png_deleted": deleted,
    }

    if not dry_run:
        result["before_mb"] = round(before / 1024 / 1024, 2)
        result["after_mb"] = round(after / 1024 / 1024, 2)
        result["saved_mb"] = round((before - after) / 1024 / 1024, 2)

    return result


# ----------------------------------------------------
# CLI
# ----------------------------------------------------

def main():

    args = parse_args()
    result = run(
        vault=args.vault,
        quality=args.quality,
        lossless=args.lossless,
        keep=args.keep,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    if "error" in result:
        print(result["error"])
        return

    print()
    print("=" * 50)
    print("Finished")
    print("=" * 50)
    print()

    print(f"PNG found           : {result['png_found']}")
    print(f"Converted           : {result['converted']}")
    print(f"Skipped             : {result['skipped']}")
    print(f"Markdown modified   : {result['markdown_modified']}")
    print(f"Image links updated : {result['links_updated']}")
    print(f"PNG deleted         : {result['png_deleted']}")

    if "saved_mb" in result:
        print()
        print(f"Before : {result['before_mb']:.2f} MB")
        print(f"After  : {result['after_mb']:.2f} MB")
        print(f"Saved  : {result['saved_mb']:.2f} MB")


if __name__ == "__main__":
    main()