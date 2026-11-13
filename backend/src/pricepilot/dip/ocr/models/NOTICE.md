# OCR models bundled with PricePilot

The models are part of the application and are loaded from this directory by explicit path.
They are never downloaded at run time, and no document or image leaves the computer.

| File | Purpose | Upstream | SHA-256 |
|---|---|---|---|
| `PP-OCRv6_det_small.onnx` | text detection (multilingual) | PaddleOCR PP-OCRv6 small det, ONNX by RapidOCR 3.10.0 (`multi_PP-OCRv6_det_small`) | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| `eslav_PP-OCRv5_rec_mobile.onnx` | recognition: Russian/East Slavic + English | PaddleOCR PP-OCRv5 eslav mobile rec, ONNX by RapidOCR 3.10.0 (`eslav_PP-OCRv5_rec_mobile`) | `08705d6721849b1347d26187f15a5e362c431963a2a62bfff4feac578c489aab` |

Both checksums equal the values published in RapidOCR 3.10.0 `default_models.yaml`
(source: `https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/...`).

License: Apache License 2.0 (`LICENSE-Apache-2.0.txt`). Model weights © Baidu / PaddleOCR
authors; ONNX conversion and inference code © RapidOCR authors. See
`RAPIDOCR_MODEL_LICENSES.md` for RapidOCR's attribution notice. That notice lists the
detection model explicitly; the eslav recognition model is distributed by the same project
from the PaddleOCR (Apache-2.0) model zoo but is not listed there by name.

The text-direction classifier is not used (it turned short upright words such as «лист»
upside down in the comparison on price lists) and is not bundled.

Updating a model changes OCR results: bump `OCR_VERSION` in `pricepilot/dip/ocr/engine.py`
and the DIP pipeline version, and re-run the OCR comparison.
