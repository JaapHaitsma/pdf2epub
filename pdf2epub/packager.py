from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Dict, Any


def write_manifest_to_dir(manifest: Dict[str, Any], out_dir: Path) -> None:
    files = manifest.get("files", [])
    for f in files:
        path = Path(out_dir, f["path"]).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f.get("content", "")
        encoding = f.get("encoding", "utf-8")
        if isinstance(content, str):
            path.write_text(content, encoding=encoding)
        else:
            # assume raw bytes; write as-is
            path.write_bytes(content)


def zip_epub_from_dir(src_dir: Path, out_file: Path) -> None:
    # EPUB requires the mimetype file be the first and uncompressed
    mimetype_path = src_dir / "mimetype"
    if not mimetype_path.exists():
        raise RuntimeError("EPUB 'mimetype' file missing in manifest output.")

    with zipfile.ZipFile(out_file, "w") as zf:
        with mimetype_path.open("rb") as f:
            zf.writestr("mimetype", f.read(), compress_type=zipfile.ZIP_STORED)

        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                full = Path(root) / name
                rel = full.relative_to(src_dir)
                if str(rel) == "mimetype":
                    continue
                zf.write(full, arcname=str(rel), compress_type=zipfile.ZIP_DEFLATED)
