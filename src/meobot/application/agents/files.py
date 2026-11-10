"""Reading what people attach to a question in Đội agent (0092).

Hermes' API server takes text and pictures only (``image_url`` parts, a 10 MB
request), so:

* a picture (PNG / JPEG / WebP / GIF, recognised by its first bytes, never by
  its name) goes to the agent as a picture;
* a PDF, Word, Excel, PowerPoint or text file is read here and its words go
  with the question;
* a skill - a ``SKILL.md``, or a ``.zip`` holding one - is read the same way
  and introduced as a skill: the agent uses it in this conversation, and
  keeping it is the usual skill proposal Emma approves;
* an SVG goes as its code (the browser also sends a PNG drawn from it, so the
  agent sees it); an Illustrator ``.ai`` (saved PDF-compatible) and a PDF with
  no words (a scan) are drawn here - page 1, ``pypdfium2`` - and kept as that
  picture instead of the file.

Everything else (video, audio, old ``.doc``, HEIC...) is refused with a
sentence saying what to send instead. Reading never trusts the file: zip
members are size-checked before they are read, XML with a DTD is refused, and
any failure of a parser becomes "Không đọc được tệp này".
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

from meobot.domain.agents.errors import AgentFileInvalidError
from meobot.domain.agents.models import (
    MAX_FILE_BYTES,
    MAX_FILE_TEXT_CHARS,
    MAX_IMAGE_BYTES,
    AttachmentKind,
)

#: What a zip member may unpack to, and all of them together.
_MAX_MEMBER_BYTES = 40 * 1024 * 1024
_MAX_UNPACKED_BYTES = 80 * 1024 * 1024
_MAX_SHEET_ROWS = 5000

_TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log", ".html"}
)
_SKILL_TEXT_EXTENSIONS = frozenset(
    {".md", ".txt", ".py", ".sh", ".yaml", ".yml", ".json", ".js", ".ts", ".csv", ".toml"}
)
_OLD_OFFICE = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}
_MEDIA_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".ogg"}
)
_IMAGE_LIKE = frozenset({".heic", ".heif", ".bmp", ".tif", ".tiff", ".avif"})
#: A page drawn for the agent is at most this wide / tall (what the models read well).
RENDER_MAX_SIDE = 2048
#: ...and smaller again while the PNG is over the picture limit.
_RENDER_FALLBACK_SIDES = (1600, 1200)
#: An embedded picture inside an SVG is noise to the agent: its base64 is cut out.
_SVG_EMBEDDED = re.compile(r"(data:[\w/+.-]+;base64,)[A-Za-z0-9+/=\s]{200,}")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


@dataclass(frozen=True, slots=True)
class ReadFile:
    """A file as APM keeps it: what it is, and the words read out of it."""

    kind: AttachmentKind
    content_type: str
    #: The words sent to the agent (None for a picture).
    text: str | None = None
    #: PDF pages, PowerPoint slides, Excel sheets.
    pages: int | None = None
    #: The words were longer than :data:`MAX_FILE_TEXT_CHARS` and were cut.
    truncated: bool = False
    #: A short line shown under the file ("Skill: soat-hop-dong").
    note: str | None = None
    #: What is kept instead of the upload (a page drawn as PNG), and its name.
    data: bytes | None = None
    name: str | None = None


def image_type(data: bytes) -> str | None:
    """The picture type from its first bytes, None when it is not one we take."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def clean_name(name: str) -> str:
    """A file name safe to keep and show: no folders, no control characters."""
    base = PurePosixPath(name.replace("\\", "/")).name
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return base[:200] or "tep"


def read_upload(name: str, data: bytes) -> ReadFile:
    """What the file is and its words; :class:`AgentFileInvalidError` when refused."""
    if not data:
        raise AgentFileInvalidError("Tệp trống.")
    if len(data) > MAX_FILE_BYTES:
        raise AgentFileInvalidError(f"Tệp lớn quá {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    suffix = PurePosixPath(name.lower()).suffix
    picture = image_type(data)
    if picture is not None:
        if len(data) > MAX_IMAGE_BYTES:
            raise AgentFileInvalidError(
                f"Ảnh lớn quá {MAX_IMAGE_BYTES // (1024 * 1024)} MB. Chụp lại hoặc thu nhỏ rồi gửi."
            )
        return ReadFile(AttachmentKind.IMAGE, picture)
    if suffix in _MEDIA_EXTENSIONS:
        raise AgentFileInvalidError(
            "Agent chưa xem được video / nghe được âm thanh. "
            "Gửi ảnh chụp màn hình, hoặc chép nội dung ra chữ."
        )
    if suffix in _IMAGE_LIKE:
        raise AgentFileInvalidError("Chỉ nhận ảnh PNG, JPG, WebP, GIF. Lưu lại dạng JPG rồi gửi.")
    if suffix in _OLD_OFFICE:
        raise AgentFileInvalidError(
            f"Tệp {suffix} đời cũ chưa đọc được. Mở bằng Word / Excel / PowerPoint, "
            f"lưu lại dạng {_OLD_OFFICE[suffix]} rồi gửi."
        )
    try:
        if suffix == ".ai":
            return _illustrator(name, data)
        if suffix == ".svg":
            return _svg(data)
        if suffix == ".pdf" or data.startswith(b"%PDF-"):
            return _pdf(name, data)
        if suffix == ".zip":
            return _skill_zip(data)
        if suffix == ".docx":
            return _docx(data)
        if suffix in (".xlsx", ".xlsm"):
            return _xlsx(data)
        if suffix == ".pptx":
            return _pptx(data)
        if suffix in _TEXT_EXTENSIONS or suffix == "":
            text = _decode(data)
            if PurePosixPath(name).name.lower() == "skill.md":
                return _skill(text, "text/markdown")
            if suffix in (".csv", ".tsv"):
                text = _table_text(text, "\t" if suffix == ".tsv" else ",")
            return _document("text/plain", text)
    except AgentFileInvalidError:
        raise
    except Exception as exc:  # a broken or hostile file: one sentence, never a trace
        raise AgentFileInvalidError(
            "Không đọc được tệp này (tệp hỏng hoặc sai định dạng)."
        ) from exc
    raise AgentFileInvalidError(
        "Chưa đọc được loại tệp này. Nhận: ảnh, SVG, Illustrator (.ai), PDF, Word (.docx), "
        "Excel (.xlsx), PowerPoint (.pptx), tệp chữ (.txt, .md, .csv) "
        "và skill (SKILL.md hoặc .zip)."
    )


# --- one kind each -------------------------------------------------------------------


def _cut(text: str) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= MAX_FILE_TEXT_CHARS:
        return text, False
    return text[:MAX_FILE_TEXT_CHARS].rstrip(), True


def _document(
    content_type: str, text: str, *, pages: int | None = None, note: str | None = None
) -> ReadFile:
    if not text.strip():
        raise AgentFileInvalidError("Tệp không có chữ nào để đọc.")
    body, truncated = _cut(text)
    if truncated:
        note = (note + " · " if note else "") + "Dài quá, agent chỉ nhận phần đầu."
    return ReadFile(
        AttachmentKind.DOCUMENT, content_type, body, pages=pages, truncated=truncated, note=note
    )


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1258", errors="replace")


def _table_text(text: str, delimiter: str) -> str:
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    return "\n".join("\t".join(cell.strip() for cell in row) for row in rows if any(row))


def _pdf(name: str, data: bytes) -> ReadFile:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise AgentFileInvalidError("PDF có mật khẩu. Bỏ mật khẩu rồi gửi lại.")
    parts: list[str] = []
    size = 0
    pages = len(reader.pages)
    for number, page in enumerate(reader.pages, start=1):
        words = (page.extract_text() or "").strip()
        if words:
            parts.append(f"--- Trang {number} ---\n{words}")
            size += len(words)
        if size > MAX_FILE_TEXT_CHARS:
            break
    if not parts:  # a scan: the agent looks at page 1 instead
        note = "PDF không có chữ (bản scan): agent xem ảnh trang 1"
        if pages > 1:
            note += f"/{pages}. Trang khác gửi dạng ảnh"
        return _page_picture(name, data, note=note)
    return _document("application/pdf", "\n\n".join(parts), pages=pages, note=f"{pages} trang")


def _illustrator(name: str, data: bytes) -> ReadFile:
    """An ``.ai`` saved PDF-compatible (Illustrator's default) is a PDF: draw its artboard."""
    if not data.startswith(b"%PDF-"):
        raise AgentFileInvalidError(
            "Tệp .ai này không lưu kèm bản PDF nên chưa xem được. Trong Illustrator: "
            "Save As bật 'Create PDF Compatible File', hoặc xuất SVG / PNG rồi gửi."
        )
    return _page_picture(name, data, note="Ảnh vẽ từ tệp Illustrator (.ai)")


def _svg(data: bytes) -> ReadFile:
    """An SVG is text: the agent reads its code (colours, shapes) and may put it in a page."""
    text = _decode(data)
    if "<svg" not in text[:20_000].lower():
        raise AgentFileInvalidError("Tệp .svg này không có hình SVG nào.")
    text = _SVG_EMBEDDED.sub(r"\1…(ảnh nhúng, đã lược)", text)
    return _document("image/svg+xml", text, note="Mã SVG (vector)")


def _page_picture(name: str, data: bytes, *, note: str) -> ReadFile:
    """Page 1 of a PDF drawn as a PNG on white, kept in place of the file."""
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise AgentFileInvalidError("Tệp có mật khẩu. Bỏ mật khẩu rồi gửi lại.") from exc
        raise
    try:
        if len(document) == 0:
            raise AgentFileInvalidError("Tệp không có trang nào.")
        page = document[0]
        width, height = page.get_size()
        if width <= 0 or height <= 0:
            raise AgentFileInvalidError("Không vẽ được trang đầu của tệp này.")
        picture = b""
        for side in (RENDER_MAX_SIDE, *_RENDER_FALLBACK_SIDES):
            bitmap = page.render(
                scale=side / max(width, height), fill_color=(255, 255, 255, 255), rev_byteorder=True
            )
            try:
                picture = _png(
                    bitmap.buffer, bitmap.width, bitmap.height, bitmap.stride, bitmap.mode
                )
            finally:
                bitmap.close()
            if len(picture) <= MAX_IMAGE_BYTES:
                break
        else:
            raise AgentFileInvalidError("Trang đầu vẽ ra lớn quá. Chụp màn hình rồi gửi dạng ảnh.")
        page.close()
    finally:
        document.close()
    stem = PurePosixPath(name).stem or "trang"
    return ReadFile(
        AttachmentKind.IMAGE,
        "image/png",
        note=note,
        data=picture,
        name=clean_name(f"{stem} - trang 1.png"),
    )


def _png(buffer: Any, width: int, height: int, stride: int, mode: str) -> bytes:
    """A PNG from raw RGB / RGBA rows (no Pillow: a filter byte per row, zlib, three chunks).
    An opaque fill with ``rev_byteorder`` renders RGB."""
    import struct
    import zlib

    channels = {"RGB": 3, "RGBA": 4}.get(mode)
    if channels is None:
        raise AgentFileInvalidError("Không vẽ được trang đầu của tệp này.")
    raw = memoryview(buffer).cast("B")
    row_bytes = width * channels
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += raw[y * stride : y * stride + row_bytes]
    colour = 6 if channels == 4 else 2

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def _members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if sum(info.file_size for info in infos) > _MAX_UNPACKED_BYTES:
        raise AgentFileInvalidError("Tệp nén giải ra lớn quá.")
    return infos


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > _MAX_MEMBER_BYTES:
        raise AgentFileInvalidError("Tệp nén có phần lớn quá.")
    with archive.open(info) as handle:
        data = handle.read(_MAX_MEMBER_BYTES + 1)
    if len(data) > _MAX_MEMBER_BYTES:
        raise AgentFileInvalidError("Tệp nén có phần lớn quá.")
    return data


def _xml(data: bytes) -> ElementTree.Element:
    head = data[:4096]
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data:
        raise AgentFileInvalidError("Không đọc được tệp này (tệp hỏng hoặc sai định dạng).")
    # No DTD, so no entity expansion; expat >= 2.4 also caps amplification itself.
    return ElementTree.fromstring(data)  # noqa: S314


def _office_part(data: bytes, name: str) -> ElementTree.Element:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = {info.filename: info for info in _members(archive)}
        if name not in infos:
            raise AgentFileInvalidError("Không đọc được tệp này (tệp hỏng hoặc sai định dạng).")
        return _xml(_read_member(archive, infos[name]))


def _paragraph(element: ElementTree.Element) -> str:
    out: list[str] = []
    for node in element.iter():
        if node.tag == f"{_W}t" and node.text:
            out.append(node.text)
        elif node.tag == f"{_W}tab":
            out.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out)


def _docx(data: bytes) -> ReadFile:
    root = _office_part(data, "word/document.xml")
    body = root.find(f"{_W}body")
    lines: list[str] = []
    for block in list(body) if body is not None else []:
        if block.tag == f"{_W}p":
            lines.append(_paragraph(block))
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = [
                    " ".join(_paragraph(p) for p in cell.iter(f"{_W}p")).strip()
                    for cell in row.iter(f"{_W}tc")
                ]
                lines.append(" | ".join(cells))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return _document(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", text
    )


def _pptx(data: bytes) -> ReadFile:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        slides = sorted(
            (
                info
                for info in _members(archive)
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", info.filename)
            ),
            key=lambda info: int(re.sub(r"\D", "", info.filename) or 0),
        )
        parts: list[str] = []
        worded = False
        for number, info in enumerate(slides, start=1):
            root = _xml(_read_member(archive, info))
            paragraphs = [
                "".join(node.text or "" for node in p.iter(f"{_A}t")) for p in root.iter(f"{_A}p")
            ]
            words = "\n".join(line for line in paragraphs if line.strip())
            worded = worded or bool(words)
            parts.append(f"--- Slide {number} ---\n{words}")
    return _document(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "\n\n".join(parts) if worded else "",
        pages=len(slides),
        note=f"{len(slides)} slide",
    )


def _xlsx(data: bytes) -> ReadFile:
    from openpyxl import load_workbook

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        _members(archive)  # size check before openpyxl unpacks anything
    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        parts: list[str] = []
        size = 0
        for sheet in book.worksheets:
            lines = [f"--- Sheet: {sheet.title} ---"]
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= _MAX_SHEET_ROWS or size > MAX_FILE_TEXT_CHARS:
                    lines.append("…")
                    break
                cells = ["" if value is None else str(value).strip() for value in row]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    line = "\t".join(cells)
                    lines.append(line)
                    size += len(line)
            parts.append("\n".join(lines))
        sheets = len(book.worksheets)
    finally:
        book.close()
    text = "\n\n".join(parts)
    if not any("\n" in part for part in parts):
        text = ""
    return _document(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        text,
        pages=sheets,
        note=f"{sheets} sheet",
    )


def _skill_name(skill_md: str) -> str | None:
    match = re.match(r"\A---\s*\n(.*?)\n---", skill_md, re.DOTALL)
    if not match:
        return None
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "name" and value.strip():
            return value.strip().strip("\"'")[:80]
    return None


def _skill(
    text: str, content_type: str, *, files: int = 1, skill_md: str | None = None
) -> ReadFile:
    if not text.strip():
        raise AgentFileInvalidError("SKILL.md trống.")
    body, truncated = _cut(text)
    name = _skill_name(text if skill_md is None else skill_md)
    note = f"Skill: {name}" if name else "Skill"
    if files > 1:
        note += f" · {files} tệp"
    if truncated:
        note += " · Dài quá, agent chỉ nhận phần đầu."
    return ReadFile(AttachmentKind.SKILL, content_type, body, truncated=truncated, note=note)


def _skill_zip(data: bytes) -> ReadFile:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = [
            info
            for info in _members(archive)
            if not info.is_dir()
            and not info.filename.startswith("__MACOSX/")
            and not any(part.startswith(".") for part in PurePosixPath(info.filename).parts)
        ]
        skills = sorted(
            (info for info in infos if PurePosixPath(info.filename).name.lower() == "skill.md"),
            key=lambda info: len(PurePosixPath(info.filename).parts),
        )
        if not skills:
            raise AgentFileInvalidError(
                "Tệp .zip chỉ nhận gói skill (có SKILL.md bên trong). "
                "Tài liệu thường thì gửi từng tệp."
            )
        root = PurePosixPath(skills[0].filename).parent
        main = _decode(_read_member(archive, skills[0]))
        blocks = [f"--- {PurePosixPath(skills[0].filename).relative_to(root)} ---\n{main}"]
        count = 1
        for info in sorted(infos, key=lambda item: item.filename):
            path = PurePosixPath(info.filename)
            if info is skills[0] or root not in (path, *path.parents):
                continue
            if path.suffix.lower() not in _SKILL_TEXT_EXTENSIONS:
                continue
            words = _decode(_read_member(archive, info))
            blocks.append(f"--- {path.relative_to(root)} ---\n{words}")
            count += 1
    return _skill("\n\n".join(blocks), "application/zip", files=count, skill_md=main)


__all__ = ["ReadFile", "clean_name", "image_type", "read_upload"]
