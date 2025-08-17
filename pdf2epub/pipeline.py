from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from .gemini_client import (
    ensure_uploaded_file,
    get_book_metadata_verbose,
    get_section_content_verbose,
    get_sections_from_pdf_verbose,
    init_client,
)
from .packager import write_manifest_to_dir, zip_epub_from_dir


def convert_pdf_to_epub(
    input_pdf: Path,
    output_epub: Path,
    api_key: str,
    model: str,
    keep_sources: bool = False,
    console: Optional[Console] = None,
    by_section: bool = False,
    debug: bool = False,
    cover_image_path: Optional[Path] = None,
    auto_cover: bool = True,
) -> None:
    console = console or Console()

    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)

    console.log(f"Reading PDF: {input_pdf}")
    client = init_client(api_key=api_key, model=model)

    # Only section-by-section mode is supported
    console.log("Using section-by-section mode…")
    manifest = _build_manifest_by_section(
        client,
        input_pdf,
        output_epub,
        console,
        debug,
        cover_image_path=cover_image_path,
        auto_cover=auto_cover,
    )
    temp_dir = output_epub.parent / (output_epub.stem + "_epub_src")
    if temp_dir.exists():
        # Best-effort clean
        import shutil
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    write_manifest_to_dir(manifest, temp_dir)
    console.log("Zipping EPUB…")
    zip_epub_from_dir(temp_dir, output_epub)
    if not keep_sources:
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except Exception:
            pass
    return


def _build_manifest_by_section(
    client,
    input_pdf: Path,
    output_epub: Path,
    console: Console,
    debug: bool,
    *,
    cover_image_path: Optional[Path] = None,
    auto_cover: bool = True,
) -> Dict[str, Any]:
    stem = output_epub.stem
    # 1) Get sections
    sections_debug = output_epub.parent / f"{stem}_sections_raw.json"
    # Ensure the PDF is uploaded once; in tests DummyModel won't have chat APIs, so skip upload
    uploaded = None
    try:
        if hasattr(client, "start_chat") or hasattr(client, "generate_content"):
            uploaded = ensure_uploaded_file(str(input_pdf), console=console)
    except Exception:
        # In non-networked test contexts, proceed without an uploaded handle.
        uploaded = None
    sections = get_sections_from_pdf_verbose(
        client,
        str(input_pdf),
        uploaded_file=uploaded,
        console=console,
        debug_path=str(sections_debug) if debug else None,
    )
    if not sections:
        # Fallback to a single generic section
        sections = [{"index": 1, "type": "section", "title": input_pdf.stem}]

    files: List[Dict[str, Any]] = []
    entries: List[Dict[str, str]] = []

    css = (
        "body{font-family:serif;line-height:1.5;} h1,h2,h3{font-family:sans-serif;} "
        "img{max-width:100%; height:auto;} code,pre{font-family:monospace;}"
    )
    files.append({"path": "OEBPS/styles.css", "content": css, "encoding": "utf-8"})

    # Track how many times we've seen a given base name to avoid duplicates
    used_counts: Dict[str, int] = {}
    used_image_names: Dict[str, int] = {}
    image_files: List[Dict[str, Any]] = []
    image_manifest: List[Dict[str, str]] = []  # {id, href, media}
    for sec in sections:
        idx = int(sec.get("index", len(entries) + 1))
        sec_type = str(sec.get("type", "section")).lower()
        title = str(sec.get("title", sec_type.title()))
        sec_debug = output_epub.parent / f"{stem}_sec{idx:02}_raw.json"
        # Best-effort page range hint from sections list
        pr = None
        try:
            ps = int(sec.get("page_start")) if sec.get("page_start") is not None else None
            pe = int(sec.get("page_end")) if sec.get("page_end") is not None else None
            if ps and pe and pe >= ps:
                pr = (ps, pe)
        except Exception:
            pr = None
        data = get_section_content_verbose(
            client,
            str(input_pdf),
            uploaded_file=uploaded,
            section_index=idx,
            section_type=sec_type,
            section_title=title,
            page_range=pr,
            console=console,
            debug_path=str(sec_debug) if debug else None,
        )
        html_fragment: str = data.get("xhtml", "")
        # Filter decorative images before extraction and strip their references from XHTML
        imgs_raw = data.get("images") if isinstance(data, dict) else None
        filtered_imgs: List[Dict[str, Any]] = []
        skip_names: set[str] = set()
        if isinstance(imgs_raw, list) and imgs_raw:
            for it in imgs_raw:
                if not isinstance(it, dict):
                    continue
                box = it.get("box_2d")
                if not isinstance(box, list) or len(box) != 4:
                    continue
                try:
                    x0, y0, x1, y1 = [float(v) for v in box]
                except Exception:
                    continue
                # compute normalized geometry features
                w = max(0.0, min(1.0, x1) - max(0.0, min(1.0, x0)))
                h = max(0.0, min(1.0, y1) - max(0.0, min(1.0, y0)))
                area = w * h
                ar = (w / h) if h > 1e-6 else float("inf")
                # Heuristics for decorative boxes around text or separators:
                # - extremely thin bands (likely lines/highlights)
                # - very large thin frames (page borders)
                # - near-full-width thin strips (separators)
                very_thin = (w < 0.01) or (h < 0.01)
                near_full_w = w > 0.95
                near_full_h = h > 0.95
                long_skinny = (ar > 10) or (ar < 0.1)
                page_border_like = (area > 0.6 and (very_thin or long_skinny))
                separator_like = (near_full_w and h < 0.02) or (near_full_h and w < 0.02)
                decorative = (very_thin or separator_like or page_border_like)
                if decorative:
                    name = str(it.get("filename", ""))
                    if name:
                        skip_names.add(name)
                    continue
                filtered_imgs.append(it)
        # Remove references in XHTML to skipped images to avoid broken links
        if skip_names:
            for nm in skip_names:
                # Remove entire <img ...> tag that references the skipped image
                pattern_dq = rf"<img\b[^>]*?\bsrc\s*=\s*\"images/{re.escape(nm)}\"[^>]*?/?>"
                pattern_sq = rf"<img\b[^>]*?\bsrc\s*=\s*'images/{re.escape(nm)}'[^>]*?/?>"
                html_fragment = re.sub(pattern_dq, "", html_fragment, flags=re.IGNORECASE)
                html_fragment = re.sub(pattern_sq, "", html_fragment, flags=re.IGNORECASE)
        # Ensure the fragment contains a top heading with the exact title (keeping numbering)
        html_fragment = _ensure_title_heading(title, html_fragment)
        xhtml = _wrap_xhtml(title, html_fragment)
        xhtml = _soft_wrap_xhtml(xhtml, width=150)
        base = _basename_from_title_or_type(title, sec_type, idx, used_counts)
        rel_href = f"{base}.xhtml"
        file_path = f"OEBPS/{rel_href}"
        files.append({"path": file_path, "content": xhtml, "encoding": "utf-8"})
        entries.append({"id": f"sec{idx:02}", "href": rel_href, "title": title, "type": sec_type})

        # Handle optional images from Gemini
        try:
            imgs = filtered_imgs
            if isinstance(imgs, list) and imgs:
                added = _extract_and_register_images(
                    input_pdf,
                    imgs,
                    used_image_names,
                    image_files,
                    image_manifest,
                    console,
                )
                if added and console:
                    try:
                        console.log(f"Added {added} image(s) for section {idx}")
                    except Exception:
                        pass
        except Exception as e:
            try:
                console.log(f"Failed to process images for section {idx}: {e}")
            except Exception:
                pass

    book_title = input_pdf.stem
    meta = _extract_metadata(client, input_pdf, output_epub, console, debug, uploaded)
    nav = _build_nav_xhtml(book_title, entries)
    nav = _soft_wrap_xhtml(nav, width=150)
    uid = meta.get("isbn") or "urn:uuid:00000000-0000-0000-0000-000000000000"
    ncx = _build_toc_ncx(uid, book_title, entries)
    # Optional cover image handling (user-provided takes precedence; else auto from first page if enabled)
    cover: Optional[Dict[str, Any]] = None
    try:
        cover = _prepare_cover(input_pdf, cover_image_path, auto_cover, console)
    except Exception as e:
        try:
            console.log(f"Cover preparation failed: {e}")
        except Exception:
            pass

    # Merge image_files into files and declare in OPF
    for img in image_files:
        files.append(img)
    # If we have a cover, add its files (image + cover.xhtml)
    if cover:
        # Add image binary
        img_href = str(cover.get("image_href", "images/cover.jpg"))
        img_bytes = cover.get("binary", b"")
        if isinstance(img_bytes, (bytes, bytearray)) and img_bytes:
            files.append({
                "path": f"OEBPS/{img_href}",
                "content": bytes(img_bytes),
                "encoding": None,
            })
        # Add cover page
        files.append({
            "path": "OEBPS/cover.xhtml",
            "content": _build_cover_xhtml(img_href),
            "encoding": "utf-8",
        })
    opf = _build_content_opf(
        book_title,
        entries,
        meta,
        include_ncx=True,
        extra_items=image_manifest,
        cover=cover,
    )
    files.append({"path": "OEBPS/nav.xhtml", "content": nav, "encoding": "utf-8"})
    files.append({"path": "OEBPS/toc.ncx", "content": ncx, "encoding": "utf-8"})
    files.append({"path": "OEBPS/content.opf", "content": opf, "encoding": "utf-8"})

    return {"files": files}


def _wrap_xhtml(title: str, body_fragment: str) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<html xmlns='http://www.w3.org/1999/xhtml' xml:lang='en'>\n"
        "  <head>\n"
        f"    <title>{_escape_xml(title)}</title>\n"
        "    <meta charset='utf-8'/>\n"
        "    <link rel='stylesheet' type='text/css' href='styles.css'/>\n"
        "  </head>\n"
        "  <body>\n"
        f"{body_fragment}\n"
        "  </body>\n"
        "</html>\n"
    )


def _build_nav_xhtml(book_title: str, entries: List[Dict[str, str]]) -> str:
    lis = "\n".join(
        f"        <li><a href='{_escape_xml(ch['href'])}'>{_escape_xml(ch['title'])}</a></li>" for ch in entries
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>\n"
        "  <head>\n"
        f"    <title>{_escape_xml(book_title)}</title>\n"
        "    <meta charset='utf-8'/>\n"
        "  </head>\n"
        "  <body>\n"
        "    <nav epub:type='toc' id='toc'>\n"
        f"      <h2>{_escape_xml(book_title)}</h2>\n"
        "      <ol>\n"
        f"{lis}\n"
        "      </ol>\n"
        "    </nav>\n"
        "  </body>\n"
        "</html>\n"
    )


def _ensure_title_heading(title: str, fragment: str) -> str:
    """Ensure the fragment begins with a heading containing the exact title.

    - If an <h1> or <h2> already contains the title text (case-insensitive, trimmed), keep as-is.
    - Otherwise, insert an <h1>{title}</h1> at the top.
    """
    t = (title or "").strip()
    if not t:
        return fragment
    # Quick check for an existing heading with the title
    pat = re.compile(r"<(h1|h2)[^>]*>\s*" + re.escape(t) + r"\s*</\1>", re.IGNORECASE)
    if pat.search(fragment):
        return fragment
    # Otherwise, prepend an h1
    return f"<h1>{_escape_xml(t)}</h1>\n" + (fragment or "")


def _build_content_opf(
    book_title: str,
    entries: List[Dict[str, str]],
    meta: Dict[str, Any] | None = None,
    include_ncx: bool = False,
    extra_items: List[Dict[str, str]] | None = None,
    cover: Optional[Dict[str, str]] = None,
) -> str:
    meta = meta or {}
    extra_items = extra_items or []
    dc_creators = "\n".join(
        f"    <dc:creator>{_escape_xml(a)}</dc:creator>" for a in meta.get("authors", [])
    )
    dc_subjects = "\n".join(
        f"    <dc:subject>{_escape_xml(s)}</dc:subject>" for s in meta.get("subjects", [])
    )
    dc_identifier = (
        f"    <dc:identifier id='bookid'>{_escape_xml(meta['isbn'])}</dc:identifier>\n"
        if meta.get("isbn") else "    <dc:identifier id='bookid'>urn:uuid:00000000-0000-0000-0000-000000000000</dc:identifier>\n"
    )
    dc_language = _escape_xml(meta.get("language") or "en")
    dc_publisher = (
        f"    <dc:publisher>{_escape_xml(meta['publisher'])}</dc:publisher>\n" if meta.get("publisher") else ""
    )
    dc_date = (
        f"    <dc:date>{_escape_xml(meta['date'])}</dc:date>\n" if meta.get("date") else ""
    )
    dc_description = (
        f"    <dc:description>{_escape_xml(meta['description'])}</dc:description>\n" if meta.get("description") else ""
    )
    manifest_items = [
        ("    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml'/>") if include_ncx
        else ("    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>")
        ,
        "    <item id='css' href='styles.css' media-type='text/css'/>",
    ]
    if include_ncx:
        manifest_items.insert(0, "    <item id='ncx' href='toc.ncx' media-type='application/x-dtbncx+xml'/>")
    # If cover present, declare cover page + image in manifest
    if cover:
        cover_page_href = _escape_xml(cover.get("page_href", "cover.xhtml"))
        cover_page_id = _escape_xml(cover.get("page_id", "cover"))
        cover_img_href = _escape_xml(cover.get("image_href", "images/cover.jpg"))
        cover_img_id = _escape_xml(cover.get("image_id", "cover-image"))
        cover_img_media = _escape_xml(cover.get("image_media", "image/jpeg"))
        manifest_items.append(f"    <item id='{cover_page_id}' href='{cover_page_href}' media-type='application/xhtml+xml'/>")
        manifest_items.append(f"    <item id='{cover_img_id}' href='{cover_img_href}' media-type='{cover_img_media}'/>")
    spine_items = []
    # Put cover first in spine if present
    if cover:
        spine_items.append("    <itemref idref='" + _escape_xml(cover.get("page_id", "cover")) + "'/>")
    for ch in entries:
        chap_id = ch["id"]
        href = ch["href"]
        manifest_items.append(
            f"    <item id='{chap_id}' href='{_escape_xml(href)}' media-type='application/xhtml+xml'/>"
        )
        spine_items.append(f"    <itemref idref='{chap_id}'/>")
    # Include supplied extra items (e.g., images)
    for it in extra_items:
        iid = _escape_xml(it.get("id", ""))
        href = _escape_xml(it.get("href", ""))
        media = _escape_xml(it.get("media", "application/octet-stream"))
        if iid and href:
            manifest_items.append(f"    <item id='{iid}' href='{href}' media-type='{media}'/>")
    manifest_xml = "\n".join(manifest_items)
    spine_xml = "\n".join(spine_items)
    pkg_version = "2.0" if include_ncx else "3.0"
    spine_open = "  <spine toc='ncx'>\n" if include_ncx else "  <spine>\n"
    # Additional metadata for cover and guide entry (EPUB 2 compatibility)
    extra_meta = ""
    guide_xml = ""
    if cover:
        extra_meta = "    <meta name='cover' content='" + _escape_xml(cover.get("image_id", "cover-image")) + "'/>\n"
        guide_xml = (
            "  <guide>\n"
            "    <reference type='cover' title='Cover' href='" + _escape_xml(cover.get("page_href", "cover.xhtml")) + "'/>\n"
            "  </guide>\n"
        )
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        f"<package xmlns='http://www.idpf.org/2007/opf' version='{pkg_version}' unique-identifier='bookid'>\n"
        "  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>\n"
        f"    <dc:title>{_escape_xml(meta.get('title') or book_title)}</dc:title>\n"
        f"{dc_creators}\n"
        f"{dc_identifier}"
        f"    <dc:language>{dc_language}</dc:language>\n"
        f"{dc_publisher}"
        f"{dc_date}"
        f"{dc_description}"
        f"{dc_subjects}\n"
        "    <meta property='dcterms:modified'>1970-01-01T00:00:00Z</meta>\n"
        f"{extra_meta}"
        "  </metadata>\n"
        "  <manifest>\n"
        f"{manifest_xml}\n"
        "  </manifest>\n"
        f"{spine_open}"
        f"{spine_xml}\n"
        "  </spine>\n"
        f"{guide_xml}"
        "</package>\n"
    )


def _build_cover_xhtml(img_href: str) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
    "<html xmlns='http://www.idpf.org/2007/ops' xmlns:xhtml='http://www.w3.org/1999/xhtml' xml:lang='en'>\n"
        "  <head>\n"
        "    <title>Cover</title>\n"
        "    <meta charset='utf-8'/>\n"
        "    <link rel='stylesheet' type='text/css' href='styles.css'/>\n"
        "    <style>body,html{margin:0;padding:0}.cover{display:flex;align-items:center;justify-content:center;min-height:98vh}img{max-width:100%;height:auto;display:block}</style>\n"
        "  </head>\n"
        "  <body>\n"
        "    <div class='cover'>\n"
    "      <img src='" + _escape_xml(img_href) + "' alt='Cover'/>\n"
        "    </div>\n"
        "  </body>\n"
        "</html>\n"
    )


def _prepare_cover(
    pdf_path: Path,
    cover_image_path: Optional[Path],
    auto_cover: bool,
    console: Optional[Console],
) -> Optional[Dict[str, str]]:
    """Return cover dict and stage the image into images/cover.jpg via manifest extra items.

    The actual OPF writing is handled in _build_content_opf; here we ensure the image
    file entry is available to be written by the packager by appending to files is not possible here,
    so we prepare a cover descriptor and also add the binary file into the global image files list
    via returned info. To keep changes minimal, we'll write the image through the extra manifest path
    by piggybacking on the main function adding files right before OPF build.
    """
    # If an explicit image is provided, use it
    if cover_image_path and cover_image_path.exists():
        try:
            data = cover_image_path.read_bytes()
            media = _mime_from_ext(cover_image_path.suffix)
            ext = (cover_image_path.suffix or ".jpg").lower().lstrip(".")
            if ext not in ("jpg", "jpeg", "png"):
                ext = "jpg"
                media = "image/jpeg"
            href = f"images/cover.{ 'jpg' if ext == 'jpeg' else ext }"
            return {
                "image_id": "cover-image",
                "image_href": href,
                "image_media": media,
                "page_id": "cover",
                "page_href": "cover.xhtml",
                "binary": data,
            }
        except Exception as e:
            if console:
                try:
                    console.log(f"Failed to read cover image: {e}")
                except Exception:
                    pass
            return None
    # auto cover from first page
    if auto_cover and pdf_path.exists():
        try:
            import importlib
            fitz = importlib.import_module("fitz")  # type: ignore[assignment]
            doc = fitz.open(str(pdf_path))
            if len(doc) > 0:
                page = doc[0]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                data = pix.tobytes("jpeg")
                # Return descriptor; caller will add file and manifest entries
                doc.close()
                return {
                    "image_id": "cover-image",
                    "image_href": "images/cover.jpg",
                    "image_media": "image/jpeg",
                    "page_id": "cover",
                    "page_href": "cover.xhtml",
                    "binary": data,
                }
            doc.close()
        except Exception as e:
            if console:
                try:
                    console.log(f"Auto cover extraction failed: {e}")
                except Exception:
                    pass
    return None

def _mime_from_ext(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    if e in ("jpg", "jpeg"):
        return "image/jpeg"
    if e == "png":
        return "image/png"
    if e == "webp":
        return "image/webp"
    return "application/octet-stream"



def _soft_wrap_xhtml(xhtml: str, width: int = 150) -> str:
    """Soft-wrap lines to a maximum width, avoiding wrapping inside <pre> and <code> blocks.

    We only break on whitespace, preserving tag and attribute integrity.
    """
    import textwrap

    lines = xhtml.splitlines()
    out: list[str] = []
    in_pre = False
    in_code = False
    for line in lines:
        # Determine wrapping eligibility for this line
        wrap_allowed = not in_pre and not in_code and ("<pre" not in line) and ("<code" not in line)
        if wrap_allowed and len(line) > width:
            # Use break_long_words=False to avoid breaking tags/attributes
            wrapped = textwrap.fill(
                line,
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
                replace_whitespace=False,
            )
            out.append(wrapped)
        else:
            out.append(line)
        # Update state after processing this line
        # Entering blocks
        if "<pre" in line:
            in_pre = True
        if "<code" in line:
            in_code = True
        # Exiting blocks
        if "</pre>" in line:
            in_pre = False
        if "</code>" in line:
            in_code = False
    return "\n".join(out)


def _build_toc_ncx(uid: str, book_title: str, entries: List[Dict[str, str]]) -> str:
    navpoints = []
    for i, ch in enumerate(entries, 1):
        title = _escape_xml(ch["title"]) if ch.get("title") else f"Section {i}"
        href = _escape_xml(ch["href"])
        navpoints.append(
            "      <navPoint id='navPoint-{i}' playOrder='{i}'>\n"
            f"        <navLabel><text>{title}</text></navLabel>\n"
            f"        <content src='{href}'/>\n"
            "      </navPoint>\n".replace("{i}", str(i))
        )
    navpoints_xml = "".join(navpoints)
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<ncx xmlns='http://www.daisy.org/z3986/2005/ncx/' version='2005-1'>\n"
        "  <head>\n"
        f"    <meta name='dtb:uid' content='{_escape_xml(uid)}'/>\n"
        "    <meta name='dtb:depth' content='1'/>\n"
        "    <meta name='dtb:totalPageCount' content='0'/>\n"
        "    <meta name='dtb:maxPageNumber' content='0'/>\n"
        "  </head>\n"
        "  <docTitle><text>" + _escape_xml(book_title) + "</text></docTitle>\n"
        "  <navMap>\n"
        f"{navpoints_xml}"
        "  </navMap>\n"
        "</ncx>\n"
    )


def _extract_metadata(client, input_pdf: Path, output_epub: Path, console: Console, debug: bool, uploaded_file=None) -> Dict[str, Any]:
    try:
        dbg_path = output_epub.parent / f"{output_epub.stem}_metadata_raw.json"
        return get_book_metadata_verbose(
            client,
            str(input_pdf),
            uploaded_file=uploaded_file,
            console=console,
            debug_path=str(dbg_path) if debug else None,
        )
    except Exception as e:  # noqa: BLE001
        try:
            console.log(f"Metadata extraction failed, continuing without: {e}")
        except Exception:
            pass
        return {}


def _escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _slugify(text: str) -> str:
    s = (text or "").strip().lower()
    out = []
    prev_hyphen = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_hyphen = False
        else:
            if not prev_hyphen:
                out.append("-")
                prev_hyphen = True
    slug = "".join(out).strip("-")
    return slug or "section"


def _basename_from_title_or_type(title: str, sec_type: str, idx: int, used_counts: Dict[str, int]) -> str:
    """Derive file basename from Gemini's section title; ensure uniqueness.

    Rules:
    - Primary: slugified title from Gemini. If empty, fallback to slugified type.
    - If still empty, fallback to "section".
    - If the same basename repeats, append a numeric suffix starting at -01, then -02, etc.
      The very first occurrence has no suffix.
    """
    base = _slugify(title) if title else ""
    if not base:
        base = _slugify(sec_type) if sec_type else "section"
    if not base:
        base = "section"
    count = used_counts.get(base, 0) + 1
    used_counts[base] = count
    if count == 1:
        return base
    # First duplicate gets -01, then -02, ...
    return f"{base}-{count - 1:02}"


def _sanitize_filename(name: str, default_ext: str = ".png") -> str:
    name = name.strip().replace("\\", "/").split("/")[-1]
    # keep only safe chars
    safe = re.sub(r"[^a-zA-Z0-9._-]", "-", name)
    if not safe:
        safe = "image"
    # ensure extension
    if "." not in safe.split("-")[-1]:
        safe += default_ext
    # normalize extension
    root, dot, ext = safe.rpartition(".")
    ext = ext.lower()
    if ext not in ("png", "jpg", "jpeg"):
        ext = "png"
    safe = (root if root else "image") + "." + ext
    return safe


def _ensure_unique_image_name(name: str, used: Dict[str, int]) -> str:
    base, dot, ext = name.rpartition(".")
    if not base:
        base = "image"
        ext = ext or "png"
    key = f"{base}.{ext}"
    count = used.get(key, 0)
    if count == 0:
        used[key] = 1
        return key
    count += 1
    used[key] = count
    return f"{base}-{count:02}.{ext}"


def _extract_and_register_images(
    pdf_path: Path,
    items: List[Dict[str, Any]],
    used_names: Dict[str, int],
    out_files: List[Dict[str, Any]],
    out_manifest: List[Dict[str, str]],
    console: Console | None,
) -> int:
    try:
        import importlib
        fitz = importlib.import_module("fitz")  # type: ignore[assignment]
    except Exception:
        if console:
            try:
                console.log("PyMuPDF not available; skipping image extraction.")
            except Exception:
                pass
        return 0
    if not pdf_path.exists():
        return 0
    doc = fitz.open(str(pdf_path))
    added = 0
    for it in items:
        filename = _sanitize_filename(str(it.get("filename", "")))
        page_index = it.get("page_index")
        box = it.get("box_2d")
        if not isinstance(page_index, int) or not isinstance(box, list) or len(box) != 4:
            continue
        pidx = page_index - 1
        if pidx < 0 or pidx >= len(doc):
            continue
        try:
            page = doc[pidx]
            rect = page.rect
            x0, y0, x1, y1 = [float(v) for v in box]
            # Normalize: accept [0..1], [0..1000], [0..10000], or absolute page points
            maxv = max(abs(x0), abs(y0), abs(x1), abs(y1))
            if maxv <= 1.01:
                nx0, ny0, nx1, ny1 = x0, y0, x1, y1
            elif maxv <= 1000.0 + 1e-6:
                nx0, ny0, nx1, ny1 = x0 / 1000.0, y0 / 1000.0, x1 / 1000.0, y1 / 1000.0
            elif maxv <= 10000.0 + 1e-6:
                nx0, ny0, nx1, ny1 = x0 / 10000.0, y0 / 10000.0, x1 / 10000.0, y1 / 10000.0
            else:
                # Assume page coordinate space (points); convert to normalized
                w = rect.width if rect.width else 1.0
                h = rect.height if rect.height else 1.0
                nx0, ny0, nx1, ny1 = x0 / w, y0 / h, x1 / w, y1 / h
            # clamp/order
            nx0, nx1 = sorted((max(0.0, min(1.0, nx0)), max(0.0, min(1.0, nx1))))
            ny0, ny1 = sorted((max(0.0, min(1.0, ny0)), max(0.0, min(1.0, ny1))))
            ax0 = rect.x0 + (rect.width * nx0)
            ay0 = rect.y0 + (rect.height * ny0)
            ax1 = rect.x0 + (rect.width * nx1)
            ay1 = rect.y0 + (rect.height * ny1)
            clip = fitz.Rect(ax0, ay0, ax1, ay1)
            # upscale a bit to improve quality
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(clip=clip, matrix=mat, alpha=False)
            # choose encoding by extension
            _, _, ext = filename.rpartition(".")
            ext = ext.lower()
            if ext == "jpg" or ext == "jpeg":
                data = pix.tobytes("jpeg")
                media = "image/jpeg"
            else:
                data = pix.tobytes("png")
                media = "image/png"
            unique = _ensure_unique_image_name(filename, used_names)
            rel = f"images/{unique}"
            out_files.append({
                "path": f"OEBPS/{rel}",
                "content": data,
                "encoding": None,
            })
            out_manifest.append({
                "id": f"img-{re.sub(r'[^a-zA-Z0-9_-]', '-', unique.rsplit('.',1)[0])}",
                "href": rel,
                "media": media,
            })
            added += 1
        except Exception:
            continue
    doc.close()
    return added
