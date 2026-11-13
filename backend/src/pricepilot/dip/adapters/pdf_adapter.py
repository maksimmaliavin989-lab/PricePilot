"""Digital PDF adapter (pdfplumber/pdfminer.six, text layer only).

Words of the text layer go through the shared layout step (``dip.layout``): lines → column
segments → one table across pages, each cell with its page and normalised box.

Pages without a text layer (scans) are rendered at 200 dpi and recognised by the bundled
local OCR; their boxes are converted back to PDF points, so text and scanned pages form one
table. Recognised cells carry the OCR confidence.
"""

from __future__ import annotations

import io

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

from pricepilot.dip.adapters.image_adapter import mean_confidence, ocr_page
from pricepilot.dip.layout import Box, PageLine, build_rows, group_lines
from pricepilot.dip.limits import Limits
from pricepilot.dip.model import FileKind, ParseError, Sheet, TableDocument
from pricepilot.dip.ocr.engine import OCR_ENGINE, OCR_VERSION, OcrEngine, shared_engine

OCR_DPI = 200


class PdfParser:
    name = "pdf"
    version = "1.1.0"
    kinds = frozenset({FileKind.PDF})

    def __init__(self, engine: OcrEngine | None = None) -> None:
        self._engine = engine

    def parse(self, data: bytes, limits: Limits) -> TableDocument:
        lines: list[PageLine] = []
        ocr_lines: list[PageLine] = []
        scanned: list[int] = []
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                if len(pdf.pages) > limits.max_pages:
                    raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_pages} страниц.")
                for number, page in enumerate(pdf.pages, start=1):
                    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
                    if not words:
                        scanned.append(number)
                        if len(scanned) > limits.max_ocr_pages:
                            raise ParseError(
                                "LIMIT_EXCEEDED",
                                f"Больше {limits.max_ocr_pages} страниц без текстового слоя "
                                "для распознавания.",
                            )
                        image = page.to_image(resolution=OCR_DPI).original
                        recognised = ocr_page(
                            self._engine or shared_engine(), image, number, OCR_DPI / 72
                        )
                        ocr_lines.extend(recognised)
                        lines.extend(recognised)
                        continue
                    boxes = [
                        Box(
                            str(w["text"]),
                            float(w["x0"]),
                            float(w["x1"]),
                            float(w["top"]),
                            float(w["bottom"]),
                        )
                        for w in words
                    ]
                    lines.extend(group_lines(boxes, number, float(page.width), float(page.height)))
                    if len(lines) > limits.max_rows:
                        raise ParseError("LIMIT_EXCEEDED", f"Больше {limits.max_rows} строк.")
                page_count = len(pdf.pages)
        except ParseError:
            raise
        except (PDFPasswordIncorrect, PDFEncryptionError) as exc:
            raise ParseError("PASSWORD_REQUIRED", "PDF защищён паролем.") from exc
        except PdfminerException as exc:
            if isinstance(
                exc.args[0] if exc.args else None, PDFPasswordIncorrect | PDFEncryptionError
            ):
                raise ParseError("PASSWORD_REQUIRED", "PDF защищён паролем.") from exc
            raise ParseError("INVALID_CONTENT", "Файл повреждён или не является PDF.") from exc
        except (PDFSyntaxError, ValueError, KeyError, OSError) as exc:
            raise ParseError("INVALID_CONTENT", "Файл повреждён или не является PDF.") from exc
        except Exception as exc:  # pdfminer raises a wide range of errors on broken files
            raise ParseError(
                "INVALID_CONTENT", "Файл повреждён: не удалось прочитать PDF."
            ) from exc

        if not lines:
            raise ParseError("NO_TABLE_FOUND", "В PDF нет текста.")

        rows, column_count = build_rows(lines, limits)
        diagnostics = {"pages": str(page_count), "columns": str(column_count)}
        if scanned:
            diagnostics |= {
                "ocr_pages": ", ".join(str(p) for p in scanned),
                "ocr_engine": OCR_ENGINE,
                "ocr_models": OCR_VERSION,
                "ocr_mean_confidence": str(mean_confidence(ocr_lines)),
            }
        return TableDocument(sheets=(Sheet(name=None, rows=tuple(rows)),), diagnostics=diagnostics)
