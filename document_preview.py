"""Render local documents for a results preview, without Tk or file changes.

Call ``render_document_preview`` from a background worker. Its detached Pillow
image can then be converted to a Tk image on the UI thread. Pages are zero-based.
"""
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from pdf_runtime import PDF_LOCK


@dataclass
class DocumentPreview:
    image: Image.Image | None = None
    page: int = 0
    page_count: int = 0
    error: str = ''


_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.ico'}
_MAX_SIZE = (1600, 2200)
_MAX_IMAGE_PIXELS = 50_000_000


def _bounds(max_size):
    try:
        width, height = max_size
        return (max(1, min(int(width), _MAX_SIZE[0])),
                max(1, min(int(height), _MAX_SIZE[1])))
    except (TypeError, ValueError, OverflowError):
        return (800, 1100)


def _page_number(page, count):
    try:
        return max(0, min(int(page), count - 1))
    except (TypeError, ValueError, OverflowError):
        return 0


def _render_pdf(path, page, bounds):
    # Keep native object destruction under the same lock as indexing and the
    # older preview. The inner function returns only detached Pillow pixels.
    with PDF_LOCK:
        return _render_pdf_locked(path, page, bounds)


def _render_pdf_locked(path, page, bounds):
    try:
        import fitz
    except ImportError:
        return DocumentPreview(error='PDF 미리보기를 준비하지 못했어요. 원본을 열어 주세요.')

    count = 0
    selected = 0
    try:
        with fitz.open(str(path)) as document:
            if document.needs_pass:
                return DocumentPreview(error='암호가 있는 PDF예요. 원본을 열어 확인해 주세요.')
            count = document.page_count
            if not count:
                return DocumentPreview(error='문서에 표시할 페이지가 없어요.')
            selected = _page_number(page, count)
            document_page = document.load_page(selected)
            rect = document_page.rect
            if rect.width <= 0 or rect.height <= 0:
                return DocumentPreview(page=selected, page_count=count,
                                       error='이 페이지를 표시할 수 없어요. 원본을 열어 주세요.')
            scale = min(bounds[0] / rect.width, bounds[1] / rect.height, 3.0)
            pixmap = document_page.get_pixmap(matrix=fitz.Matrix(scale, scale),
                                              colorspace=fitz.csRGB, alpha=False)
            image = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
            # MuPDF rounds pixel edges up; keep the returned image within bounds.
            image.thumbnail(bounds, Image.Resampling.LANCZOS)
            return DocumentPreview(image=image, page=selected, page_count=count)
    except FileNotFoundError:
        return DocumentPreview(error='파일이 이동되거나 삭제됐어요.')
    except PermissionError:
        return DocumentPreview(error='파일을 읽을 수 없어요. 원본을 열어 확인해 주세요.')
    except Exception:
        return DocumentPreview(page=selected, page_count=count,
                               error='PDF를 미리 볼 수 없어요. 원본을 열어 확인해 주세요.')


def _render_image(path, bounds):
    try:
        with Image.open(path) as source:
            if source.width * source.height > _MAX_IMAGE_PIXELS:
                return DocumentPreview(error='사진이 너무 커서 미리 볼 수 없어요. 원본을 열어 주세요.')
            # JPEG can downsample during decoding before loading all pixels.
            source.draft('RGB', bounds)
            image = ImageOps.exif_transpose(source)
            image.thumbnail(bounds, Image.Resampling.LANCZOS)
            # Transparent scans/icons get a white document background.
            rgba = image.convert('RGBA')
            result = Image.new('RGB', rgba.size, 'white')
            result.paste(rgba, mask=rgba.getchannel('A'))
            return DocumentPreview(image=result, page_count=1)
    except FileNotFoundError:
        return DocumentPreview(error='파일이 이동되거나 삭제됐어요.')
    except PermissionError:
        return DocumentPreview(error='파일을 읽을 수 없어요. 원본을 열어 확인해 주세요.')
    except Exception:
        return DocumentPreview(error='사진을 미리 볼 수 없어요. 원본을 열어 확인해 주세요.')


def render_document_preview(row: dict, page: int = 0,
                            max_size: tuple = (800, 1100)) -> DocumentPreview:
    """Read a PDF page or image; never substitute unrelated indexed thumbnails.

    ``error`` is a short displayable fallback, empty on success. The returned
    image owns its pixels and remains usable after the source file is closed.
    Rendering and decoding are synchronous; schedule this function off the UI
    thread. Output dimensions are capped even if a caller requests a huge image.
    """
    try:
        value = row.get('path')
        if not value:
            return DocumentPreview(error='파일 위치를 찾을 수 없어요.')
        path = Path(value)
        if not path.is_file():
            return DocumentPreview(error='파일이 이동되거나 삭제됐어요.')
    except (OSError, TypeError, ValueError):
        return DocumentPreview(error='파일을 읽을 수 없어요. 원본을 열어 확인해 주세요.')
    bounds = _bounds(max_size)
    extension = path.suffix.lower()
    if extension == '.pdf':
        return _render_pdf(path, page, bounds)
    if extension in _IMAGE_EXTENSIONS:
        return _render_image(path, bounds)
    return DocumentPreview(error='이 파일은 원본을 열어 확인해 주세요.')
