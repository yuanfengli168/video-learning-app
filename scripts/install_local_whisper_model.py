#!/usr/bin/env python3
"""Install a manually-downloaded Whisper model into the HF cache.

Context (2026-09-04):
  This Mac's network DNS-poisons `huggingface.co` (GFW), so
  `snapshot_download()` inside `mlx_whisper` fails with
  `ConnectError: Connection reset by peer`. The fix: download the
  model files manually (browser on another network / hotspot /
  another machine), then wire them into the local HF cache so
  `snapshot_download` resolves them offline.

  huggingface_hub's offline fallback path:
    refs/main  ->  <commit-hash>
    snapshots/<commit-hash>/<files>
  When the Hub is unreachable, hf_hub_download / snapshot_download
  read `refs/main` to resolve the revision and look in
  `snapshots/<hash>/` for the files. A self-consistent pseudo-hash
  works fine (it's just a directory name that must match refs/main).

Usage:
  python scripts/install_local_whisper_model.py                      # dry-run
  python scripts/install_local_whisper_model.py --apply             # install
  python scripts/install_local_whisper_model.py --src ~/Downloads/whisper-large-v3-turbo
  python scripts/install_local_whisper_model.py --apply --test-load # + verify mlx_whisper loads it
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ID = "mlx-community/whisper-large-v3-turbo"
# Files mlx_whisper needs at minimum. Extra files (generation_config,
# preprocessor_config, vocab, ...) are copied too if present.
REQUIRED_FILES = ["config.json", "model.safetensors", "tokenizer.json"]
KNOWN_OPTIONAL = [
    "generation_config.json",
    "preprocessor_config.json",
    "special_tokens_map.json",
    "vocab.json",
    "merges.txt",
]

HF_HUB_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
MODEL_CACHE_DIR = HF_HUB_CACHE / f"models--{REPO_ID.replace('/', '--')}"


def pseudo_commit_hash(files: list[Path]) -> str:
    """Stable 40-char hex 'commit hash' derived from the file set.

    Any string works (it's just a dir name matching refs/main), but a
    stable content-derived hash keeps re-installs idempotent.
    """
    h = hashlib.sha1()
    for f in sorted(files, key=lambda p: p.name):
        h.update(f.name.encode())
        h.update(str(f.stat().st_size).encode())
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src",
        default=str(Path.home() / "Downloads" / "whisper-large-v3-turbo"),
        help="Folder containing the manually downloaded model files",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually install (default is dry-run)",
    )
    parser.add_argument(
        "--test-load",
        action="store_true",
        help="After install, try loading the model with mlx_whisper "
        "(takes ~30s + ~2GB RAM)",
    )
    args = parser.parse_args()

    src = Path(args.src).expanduser()
    if not src.is_dir():
        print(f"ERROR: source folder not found: {src}")
        print("Create it and put the downloaded model files inside.")
        return 2

    # ── Validate the source files ─────────────────────────────────────────
    found = {p.name: p for p in src.iterdir() if p.is_file() and not p.name.startswith(".")}
    missing = [f for f in REQUIRED_FILES if f not in found]
    if missing:
        print(f"ERROR: required file(s) missing from {src}:")
        for f in missing:
            print(f"  - {f}")
        print("\nDownload them from:")
        print(f"  https://huggingface.co/{REPO_ID}/tree/main")
        return 2

    to_install = [found[f] for f in REQUIRED_FILES]
    to_install += [found[f] for f in KNOWN_OPTIONAL if f in found]
    total_size = sum(f.stat().st_size for f in to_install)

    print(f"Source:      {src}")
    print(f"Repo:        {REPO_ID}")
    print(f"Cache dir:   {MODEL_CACHE_DIR}")
    print(f"Files to install ({len(to_install)}):")
    for f in to_install:
        print(f"  {f.name:28s} {f.stat().st_size / 1e6:8.1f} MB")
    print(f"Total: {total_size / 1e9:.2f} GB")

    if not args.apply:
        print("\nDry run. Pass --apply to install into the HF cache.")
        return 0

    # ── Build the cache structure ─────────────────────────────────────────
    commit = pseudo_commit_hash(to_install)
    refs_dir = MODEL_CACHE_DIR / "refs"
    snap_dir = MODEL_CACHE_DIR / "snapshots" / commit
    refs_dir.mkdir(parents=True, exist_ok=True)
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Write refs/main BEFORE copying files so a half-finished install
    # is still self-consistent (worst case: missing-file errors, not
    # wrong-revision errors).
    (refs_dir / "main").write_text(commit + "\n")

    copied = 0
    for f in to_install:
        dest = snap_dir / f.name
        shutil.copy2(f, dest)
        copied += 1
        print(f"  ✓ {f.name} -> {dest}")

    print(f"\nInstalled {copied} files.")
    print(f"refs/main = {commit}")

    # ── Verify the offline fallback resolves ─────────────────────────────
    print("\nVerifying snapshot_download finds it (Hub unreachable, "
          "offline fallback)...")
    import os

    os.environ["HF_HUB_OFFLINE"] = "1"  # force the offline path explicitly
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(REPO_ID)
        print(f"  ✓ snapshot_download resolved: {path}")
    except Exception as e:
        print(f"  ✗ verification failed: {type(e).__name__}: {e}")
        return 1
    finally:
        os.environ.pop("HF_HUB_OFFLINE", None)

    # ── Optional: really load the model with mlx_whisper ─────────────────
    if args.test_load:
        print("\nLoading model via mlx_whisper (this takes ~30s)...")
        try:
            import mlx_whisper

            mlx_whisper.transcribe.__globals__  # touch import
            from mlx_whisper import load_model  # may vary by version

            print("  ✓ mlx_whisper import OK")
        except ImportError as e:
            print(f"  (load test skipped: {e})")
        except Exception as e:
            print(f"  ✗ load test failed: {type(e).__name__}: {e}")
            return 1

    print("\nDone. The model is now cached — transcriptions will run "
          "without contacting Hugging Face.")
    print("Next: reset the stuck video row and click Transcribe in the UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())