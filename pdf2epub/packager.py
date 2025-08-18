import os
import zipfile
from pathlib import Path
from typing import Any, Dict


def write_manifest_to_dir(manifest: Dict[str, Any], out_dir: Path) -> None:
    files = manifest.get("files", [])
    if not isinstance(files, list):
        raise RuntimeError("Manifest 'files' must be a list")
    # Ensure mimetype exists and is correct; if missing, add it
    has_mimetype = any(isinstance(f, dict) and f.get("path") == "mimetype" for f in files)
    if not has_mimetype:
        files.insert(0, {"path": "mimetype", "content": "application/epub+zip", "encoding": "utf-8"})
    # Ensure META-INF/container.xml exists; if missing, add minimal
    has_container = any(isinstance(f, dict) and f.get("path") == "META-INF/container.xml" for f in files)
    if not has_container:
        container_xml = (
            "<?xml version='1.0' encoding='utf-8'?>\n"
            "<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>\n"
            "  <rootfiles>\n"
            "    <rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/>\n"
            "  </rootfiles>\n"
            "</container>\n"
        )
        files.append({"path": "META-INF/container.xml", "content": container_xml, "encoding": "utf-8"})
    for f in files:
        if not isinstance(f, dict) or "path" not in f:
            raise RuntimeError("Each manifest file must be an object with a 'path'")
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
