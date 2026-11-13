"""Images of price lists (JPEG, PNG, TIFF) through local OCR.

Every page (TIFF frame) is recognised by the bundled RapidOCR models, word boxes go through
the shared layout step (``dip.layout``) like digital PDF words, and each cell keeps its page,
its box normalised to the image and the OCR confidence. HEIC is recognised by the sniffer but
not decoded yet (no bundled decoder) and stays an explicit ``UNSUPPORTED_FORMAT``.
"""

from __future__ import annotations

import io
import warnings
from typing import Any

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from pricepilot.dip.layout import Box, PageLine, build_rows, group_lines, split_fused_words
from pricepilot.dip.limits import Limits
from pricepilot.dip.model import FileKind, ParseError, Sheet, TableDocument
from pricepilot.dip.ocr.engine import OCR_ENGINE, OCR_VERSION, OcrEngine, shared_engine


def ocr_page(
    engine: OcrEngine, image: Image.Image, page: int, scale: float = 1.0
) -> list[PageLine]:
    """Recognise one page image. ``scale`` converts image pixels to page units (PDF: points)."""
    rgb: np.ndarray[Any, Any] = np.asarray(image.convert("RGB"))
    boxes = [
        piece
        for box in engine.recognize(rgb)
        for piece in split_fused_words(
            Box(
                box.text,
                box.x0 / scale,
                box.x1 / scale,
                box.top / scale,
                box.bottom / scale,
                confidence=box.confidence,
            )
        )
    ]
    return group_lines(boxes, page, image.width / scale, image.height / scale)


def mean_confidence(lines: list[PageLine]) -> float | None:
    values = [s.confidence for ln in lines for s in ln.segments if s.confidence is not None]
    return round(sum(values) / len(values), 3) if values else None


class ImageParser:
    name = "image-ocr"
    version = "1.0.0"
    kinds = frozenset({FileKind.JPEG, FileKind.PNG, FileKind.TIFF})

    def __init__(self, engine: OcrEngine | None = None) -> None:
        self._engine = engine

    def parse(self, data: bytes, limits: Limits) -> TableDocument:
        engine = self._engine or shared_engine()
        lines: list[PageLine] = []
        pages = 0
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(data))
                frames = getattr(image, "n_frames", 1)
                if frames > limits.max_ocr_pages:
                    raise ParseError(
                        "LIMIT_EXCEEDED",
                        f"Больше {limits.max_ocr_pages} страниц для распознавания.",
                    )
                for index in range(frames):
                    image.seek(index)
                    if image.width * image.height > limits.max_image_pixels:
                        raise ParseError(
                            "LIMIT_EXCEEDED",
                            f"Изображение больше {limits.max_image_pixels // 1_000_000} Мпикс.",
                        )
                    page = ImageOps.exif_transpose(image) or image  # phone photos
                    page.load()
                    pages += 1
                    lines.extend(ocr_page(engine, page, pages))
                    if len(lines) > limits.max_rows:
                        raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
        except ParseError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ParseError("LIMIT_EXCEEDED", "Изображение слишком большое.") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as exc:
            raise ParseError("INVALID_CONTENT", "Изображение повреждено.") from exc

        if not lines:
            raise ParseError("NO_TABLE_FOUND", "На изображении не найден текст.")
        rows, column_count = build_rows(lines, limits)
        return TableDocument(
            sheets=(Sheet(name=None, rows=tuple(rows)),),
            diagnostics={
                "pages": str(pages),
                "ocr_pages": str(pages),
                "columns": str(column_count),
                "ocr_engine": OCR_ENGINE,
                "ocr_models": OCR_VERSION,
                "ocr_mean_confidence": str(mean_confidence(lines)),
            },
        )
