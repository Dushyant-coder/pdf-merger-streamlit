"""PDF toolkit — merge PDFs/images into one PDF, or split a PDF back apart."""

import re
import zipfile
from io import BytesIO

import pandas as pd
import pymupdf
import streamlit as st
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

st.set_page_config(page_title="PDF toolkit", page_icon=":material/picture_as_pdf:", layout="centered")

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}


def file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower()


@st.cache_data(show_spinner=False, max_entries=50)
def image_to_pdf_bytes(file_bytes: bytes) -> bytes:
    """Wrap an image as a single full-page PDF without re-encoding its pixel data."""
    image = ImageOps.exif_transpose(Image.open(BytesIO(file_bytes)))

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
        image = background
    elif image.mode not in ("RGB", "L", "CMYK"):
        image = image.convert("RGB")

    buffer = BytesIO()
    image.save(buffer, format="PDF")
    return buffer.getvalue()


@st.cache_data(show_spinner=False, max_entries=50)
def read_page_count(filename: str, file_bytes: bytes) -> int | None:
    try:
        if file_extension(filename) in IMAGE_EXTENSIONS:
            return 1
        return len(PdfReader(BytesIO(file_bytes)).pages)
    except (PdfReadError, OSError):
        return None


def sync_file_state(uploaded_files) -> None:
    """Keep session_state row metadata (order, display name) in sync with the uploader."""
    st.session_state.setdefault("file_rows", {})
    current_keys = set()
    for position, file in enumerate(uploaded_files):
        key = f"{file.name}::{file.size}"
        current_keys.add(key)
        if key not in st.session_state.file_rows:
            st.session_state.file_rows[key] = {
                "order": position + 1,
                "display_name": file.name.rsplit(".", 1)[0],
            }
    # Drop rows for files the user removed from the uploader.
    for stale_key in set(st.session_state.file_rows) - current_keys:
        del st.session_state.file_rows[stale_key]


def merge_pdfs(uploaded_files, ordered_keys, add_bookmarks) -> bytes | None:
    writer = PdfWriter()
    files_by_key = {f"{f.name}::{f.size}": f for f in uploaded_files}

    for key in ordered_keys:
        file = files_by_key[key]
        file_bytes = file.getvalue()

        if file_extension(file.name) in IMAGE_EXTENSIONS:
            try:
                reader = PdfReader(BytesIO(image_to_pdf_bytes(file_bytes)))
            except Exception:
                st.error(f"Couldn't read image '{file.name}' — it was skipped.", icon=":material/error:")
                continue
        else:
            reader = PdfReader(BytesIO(file_bytes))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    st.error(f"'{file.name}' is password-protected and was skipped.", icon=":material/error:")
                    continue

        start_page = len(writer.pages)
        writer.append(reader)

        if add_bookmarks:
            display_name = st.session_state.file_rows[key]["display_name"]
            writer.add_outline_item(display_name, start_page)

    if len(writer.pages) == 0:
        return None

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def parse_page_ranges(spec: str, total_pages: int) -> list[list[int]]:
    """Parse '1-3,5,8-10' into groups of 1-indexed page numbers, one list per comma part."""
    spec = spec.strip()
    if not spec:
        raise ValueError("Enter at least one page or range, e.g. 1-3,5.")

    groups = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue

        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
        elif token.isdigit():
            start = end = int(token)
        else:
            raise ValueError(f"'{token}' isn't a valid page or range.")

        if start < 1 or end > total_pages:
            raise ValueError(f"'{token}' is outside 1-{total_pages}.")

        groups.append(list(range(start, end + 1)))

    if not groups:
        raise ValueError("Enter at least one page or range, e.g. 1-3,5.")
    return groups


def render_pages_to_images(
    pdf_bytes: bytes, pages: list[int], image_format: str, dpi: int, jpeg_quality: int
) -> list[tuple[str, bytes]]:
    results = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    zoom = dpi / 72
    matrix = pymupdf.Matrix(zoom, zoom)
    try:
        for page_num in pages:
            pix = doc[page_num - 1].get_pixmap(matrix=matrix)
            if image_format == "PNG":
                data, ext = pix.tobytes(output="png"), "png"
            else:
                data, ext = pix.tobytes(output="jpg", jpg_quality=jpeg_quality), "jpg"
            results.append((f"page_{page_num:03d}.{ext}", data))
    finally:
        doc.close()
    return results


def split_pdf(pdf_bytes: bytes, groups: list[list[int]]) -> list[tuple[str, bytes]]:
    reader = PdfReader(BytesIO(pdf_bytes))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("This PDF is password-protected.")

    results = []
    for pages in groups:
        writer = PdfWriter()
        for page_num in pages:
            writer.add_page(reader.pages[page_num - 1])

        buffer = BytesIO()
        writer.write(buffer)

        name = f"page_{pages[0]:03d}.pdf" if len(pages) == 1 else f"pages_{pages[0]:03d}-{pages[-1]:03d}.pdf"
        results.append((name, buffer.getvalue()))
    return results


def build_zip(files: list[tuple[str, bytes]]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return buffer.getvalue()


st.title("PDF toolkit")

merge_tab, split_tab = st.tabs(["Merge files", "Split & convert"])

with merge_tab:
    st.caption(
        "Merges PDFs and images into one PDF. Existing PDF pages are copied directly "
        "(no re-encoding), and each image becomes a full page without recompression — "
        "quality is preserved exactly."
    )

    uploaded_files = st.file_uploader(
        "Select PDF or image files",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="merge_uploader",
    )

    if uploaded_files:
        sync_file_state(uploaded_files)

    files_tab, output_tab = st.tabs(["Files & order", "Output file name"])

    with files_tab:
        if not uploaded_files:
            st.info("Upload two or more PDF or image files to get started.", icon=":material/upload_file:")
        else:
            rows = []
            for file in uploaded_files:
                key = f"{file.name}::{file.size}"
                row_state = st.session_state.file_rows[key]
                rows.append(
                    {
                        "key": key,
                        "Order": row_state["order"],
                        "Display name": row_state["display_name"],
                        "Original file": file.name,
                        "Pages": read_page_count(file.name, file.getvalue()),
                    }
                )

            table = pd.DataFrame(rows).sort_values("Order").reset_index(drop=True)

            edited = st.data_editor(
                table,
                column_config={
                    "key": None,
                    "Order": st.column_config.NumberColumn(
                        "Order", help="Merge order — lower numbers come first.", min_value=1, step=1
                    ),
                    "Display name": st.column_config.TextColumn(
                        "Display name", help="Used as the bookmark title for this file in the merged PDF."
                    ),
                    "Original file": st.column_config.TextColumn("Original file", disabled=True),
                    "Pages": st.column_config.NumberColumn("Pages", disabled=True),
                },
                hide_index=True,
                key="file_order_editor",
            )

            for _, row in edited.iterrows():
                st.session_state.file_rows[row["key"]]["order"] = row["Order"]
                st.session_state.file_rows[row["key"]]["display_name"] = row["Display name"]

    with output_tab:
        output_filename = st.text_input(
            "Output file name",
            value=st.session_state.get("output_filename", "merged"),
            key="output_filename",
            help="The .pdf extension is added automatically.",
        )
        add_bookmarks = st.checkbox(
            "Add a bookmark for each file, using its display name",
            value=True,
            key="add_bookmarks",
        )

        merge_clicked = st.button(
            "Merge files",
            type="primary",
            icon=":material/merge:",
            disabled=not uploaded_files or len(uploaded_files) < 2,
        )

        if uploaded_files and len(uploaded_files) < 2:
            st.caption("Upload at least two files to merge.")

        if merge_clicked:
            ordered_keys = [
                key
                for key, _ in sorted(
                    st.session_state.file_rows.items(), key=lambda item: item[1]["order"]
                )
            ]
            with st.spinner("Merging..."):
                merged_bytes = merge_pdfs(uploaded_files, ordered_keys, add_bookmarks)

            if merged_bytes is None:
                st.error("No pages could be merged.", icon=":material/error:")
            else:
                st.session_state["merged_pdf_bytes"] = merged_bytes
                st.session_state["merged_pdf_filename"] = f"{output_filename or 'merged'}.pdf"
                st.success("Merge complete.", icon=":material/check_circle:")

        if st.session_state.get("merged_pdf_bytes"):
            st.download_button(
                "Download merged PDF",
                data=st.session_state["merged_pdf_bytes"],
                file_name=st.session_state["merged_pdf_filename"],
                mime="application/pdf",
                icon=":material/download:",
            )

with split_tab:
    st.caption(
        "Upload one PDF to turn its pages into images, or split it apart into separate PDF files."
    )

    source_file = st.file_uploader(
        "Select a PDF file",
        type="pdf",
        accept_multiple_files=False,
        key="split_source_file",
    )

    if source_file:
        source_key = f"{source_file.name}::{source_file.size}"
        if st.session_state.get("split_source_key") != source_key:
            st.session_state["split_source_key"] = source_key
            st.session_state.pop("split_result", None)
            st.session_state.pop("split_result_name", None)
            st.session_state.pop("split_result_mime", None)

        try:
            total_pages = len(PdfReader(BytesIO(source_file.getvalue())).pages)
        except PdfReadError:
            st.error("Couldn't read this PDF.", icon=":material/error:")
            total_pages = None

        if total_pages:
            st.caption(f"{source_file.name} — {total_pages} page{'s' if total_pages != 1 else ''}")

            action = st.segmented_control(
                "What do you want to do?",
                ["Convert pages to images", "Split into separate PDFs"],
                default="Convert pages to images",
                key="split_action",
            )

            page_spec = st.text_input(
                "Pages to include",
                value=f"1-{total_pages}",
                help="e.g. 1-3,5,8-10. Leave as the full range to use every page.",
                key="split_page_spec",
            )

            if action == "Convert pages to images":
                image_format = st.segmented_control(
                    "Image format", ["PNG", "JPEG"], default="PNG", key="image_format"
                )
                dpi = st.select_slider(
                    "Resolution (DPI)", options=[72, 100, 150, 200, 300, 600], value=200, key="image_dpi"
                )
                jpeg_quality = 95
                if image_format == "JPEG":
                    jpeg_quality = st.slider(
                        "JPEG quality", min_value=50, max_value=100, value=95, key="jpeg_quality"
                    )

                convert_clicked = st.button(
                    "Convert to images", type="primary", icon=":material/image:", key="convert_button"
                )

                if convert_clicked:
                    try:
                        groups = parse_page_ranges(page_spec, total_pages)
                    except ValueError as e:
                        st.error(str(e), icon=":material/error:")
                    else:
                        pages = sorted({p for group in groups for p in group})
                        with st.spinner("Rendering pages..."):
                            images = render_pages_to_images(
                                source_file.getvalue(), pages, image_format, dpi, jpeg_quality
                            )

                        base = source_file.name.rsplit(".", 1)[0]
                        if len(images) == 1:
                            name, data = images[0]
                            st.session_state["split_result"] = data
                            st.session_state["split_result_name"] = f"{base}_{name}"
                            st.session_state["split_result_mime"] = (
                                "image/png" if image_format == "PNG" else "image/jpeg"
                            )
                        else:
                            st.session_state["split_result"] = build_zip(images)
                            st.session_state["split_result_name"] = f"{base}_images.zip"
                            st.session_state["split_result_mime"] = "application/zip"
                        st.success(f"Converted {len(images)} page(s).", icon=":material/check_circle:")

            else:
                keep_grouped = st.checkbox(
                    "Keep each comma-separated part together as one file",
                    value=False,
                    help=(
                        "Unchecked: every page becomes its own single-page PDF. "
                        "Checked: a range like 1-3 stays combined into one file."
                    ),
                    key="split_keep_grouped",
                )

                split_clicked = st.button(
                    "Split PDF", type="primary", icon=":material/call_split:", key="split_button"
                )

                if split_clicked:
                    try:
                        groups = parse_page_ranges(page_spec, total_pages)
                    except ValueError as e:
                        st.error(str(e), icon=":material/error:")
                    else:
                        if not keep_grouped:
                            groups = [[p] for group in groups for p in group]

                        try:
                            with st.spinner("Splitting..."):
                                parts = split_pdf(source_file.getvalue(), groups)
                        except ValueError as e:
                            st.error(str(e), icon=":material/error:")
                        else:
                            base = source_file.name.rsplit(".", 1)[0]
                            if len(parts) == 1:
                                name, data = parts[0]
                                st.session_state["split_result"] = data
                                st.session_state["split_result_name"] = f"{base}_{name}"
                                st.session_state["split_result_mime"] = "application/pdf"
                            else:
                                st.session_state["split_result"] = build_zip(parts)
                                st.session_state["split_result_name"] = f"{base}_split.zip"
                                st.session_state["split_result_mime"] = "application/zip"
                            st.success(f"Created {len(parts)} file(s).", icon=":material/check_circle:")

            if st.session_state.get("split_result"):
                st.download_button(
                    "Download",
                    data=st.session_state["split_result"],
                    file_name=st.session_state["split_result_name"],
                    mime=st.session_state["split_result_mime"],
                    icon=":material/download:",
                    key="split_download_button",
                )
    else:
        st.info("Upload a PDF to get started.", icon=":material/upload_file:")
