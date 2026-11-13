"""OCR engine port and its RapidOCR implementation.

Models are loaded by explicit file path from the bundled ``models`` directory, so RapidOCR's
model downloader is never reached: OCR works without network, and images never leave the
computer. The engine is created lazily (loading takes ~1 s) and shared; calls are serialised
because the ONNX sessions are not documented as re-entrant.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from pricepilot.dip.layout import Box
from pricepilot.dip.model import ParseError
from pricepilot.dip.ocr_text import clean_ocr_text

OCR_ENGINE = "rapidocr-3.10.0"
OCR_VERSION = "ppocr-v6det-small+v5rec-eslav-mobile"
MODELS_DIR = Path(__file__).resolve().parent / "models"
DET_MODEL = "PP-OCRv6_det_small.onnx"
REC_MODEL = "eslav_PP-OCRv5_rec_mobile.onnx"


class OcrEngine(Protocol):
    def recognize(self, image: np.ndarray[Any, Any]) -> list[Box]:
        """Word boxes in image pixels; ``confidence`` 0..1 on every box."""
        ...


class RapidOcrEngine:
    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        self._models_dir = models_dir
        self._engine: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        det, rec = self._models_dir / DET_MODEL, self._models_dir / REC_MODEL
        if not det.is_file() or not rec.is_file():
            raise ParseError(
                "OCR_RESOURCE_MISSING",
                "Не найдены модели распознавания текста в составе приложения. "
                "Переустановите PricePilot. Оригинал сохранён.",
            )
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        return RapidOCR(
            params={
                "Global.log_level": "warning",
                "Global.use_cls": False,
                "Det.model_path": str(det),
                "Rec.model_path": str(rec),
                "Rec.lang_type": LangRec.ESLAV,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            }
        )

    def recognize(self, image: np.ndarray[Any, Any]) -> list[Box]:
        with self._lock:
            if self._engine is None:
                self._engine = self._load()
            try:
                out = self._engine(image, return_word_box=True)
            except Exception as exc:  # onnxruntime/opencv errors on unusual images
                raise ParseError(
                    "INVALID_CONTENT", "Не удалось распознать текст на изображении."
                ) from exc
        if out.boxes is None or out.txts is None:
            return []
        boxes: list[Box] = []
        for line_box, words in zip(out.boxes, out.word_results or (), strict=False):
            top = float(min(p[1] for p in line_box))
            bottom = float(max(p[1] for p in line_box))
            for word in words:
                text, score, word_box = word[0], word[1], word[2]
                cleaned, _ = clean_ocr_text(str(text))
                if not cleaned:
                    continue
                xs = [float(p[0]) for p in word_box]
                boxes.append(Box(cleaned, min(xs), max(xs), top, bottom, confidence=float(score)))
        return boxes


_shared: RapidOcrEngine | None = None
_shared_lock = threading.Lock()


def shared_engine() -> RapidOcrEngine:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = RapidOcrEngine()
        return _shared
