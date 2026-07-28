from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

from core import bundled_path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTENSIONS = {".mp4"}
PERSON_LABEL = 0


@dataclass(frozen=True)
class DetectionResult:
    detected: bool
    confidence: float
    reason: str
    elapsed_seconds: float
    passes: int = 1
    error: bool = False


@dataclass(frozen=True)
class PersonEvidence:
    confidence: float = 0.0
    max_area_ratio: float = 0.0
    candidate_count: int = 0


class ModelUnavailableError(RuntimeError):
    pass


def _opencv_safe_model_path(source: Path) -> Path:
    """Copy a model to an ASCII temp path for OpenCV on Unicode profiles."""
    temp_root = Path(os.environ.get("TEMP", os.environ.get("TMP", ".")))
    cache = temp_root / "ai_media_tagger_models"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / source.name
    if not target.is_file() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)
    return target


def _letterbox(image: Image.Image, size: int = 640) -> np.ndarray:
    """Resize exactly as the custom D-FINE training pipeline does."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    resized = image.resize((size, size), Image.Resampling.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    return np.transpose(array, (2, 0, 1))[None, ...]


def _tiles(image: Image.Image) -> Iterable[Image.Image]:
    """Yield four overlapping enlarged views to reduce small-person misses."""
    width, height = image.size
    if width < 900 and height < 900:
        return
    tile_width = max(1, int(width * 0.62))
    tile_height = max(1, int(height * 0.62))
    starts_x = (0, max(0, width - tile_width))
    starts_y = (0, max(0, height - tile_height))
    for top in starts_y:
        for left in starts_x:
            yield image.crop((left, top, left + tile_width, top + tile_height))


class PersonDetector:
    """High-recall offline face/person detector; hand and foot labels are ignored."""

    def __init__(
        self,
        detection_threshold: float = 0.35,
        face_threshold: float = 0.55,
        enable_tiles: bool = True,
    ) -> None:
        self.detection_threshold = detection_threshold
        self.face_threshold = face_threshold
        self.enable_tiles = enable_tiles
        detector_path = bundled_path("models/dfine_m_human_parts_trial.onnx")
        face_path = bundled_path("models/face_detection_yunet_2023mar.onnx")
        missing = [str(path) for path in (detector_path, face_path) if not path.is_file()]
        if missing:
            raise ModelUnavailableError("缺少人物识别模型：" + "；".join(missing))

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = 0
        try:
            self.session = ort.InferenceSession(
                str(detector_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            safe_face_path = _opencv_safe_model_path(face_path)
            self.face_detector = cv2.FaceDetectorYN.create(
                str(safe_face_path), "", (320, 320), self.face_threshold, 0.3, 5000
            )
        except Exception as exc:
            raise ModelUnavailableError(f"人物识别模型加载失败：{exc}") from exc

    def _person_evidence(self, image: Image.Image) -> PersonEvidence:
        input_data = _letterbox(image)
        size = np.asarray([[640, 640]], dtype=np.int64)
        labels, boxes, scores = self.session.run(
            None, {"images": input_data, "orig_target_sizes": size}
        )
        labels = np.asarray(labels).reshape(-1)
        boxes = np.asarray(boxes).reshape(-1, 4)
        scores = np.asarray(scores).reshape(-1)
        confidence = 0.0
        max_area_ratio = 0.0
        count = 0
        for label, box, score in zip(labels, boxes, scores):
            score = float(score)
            # The custom model's labels 1 and 2 are hand/arm and foot/leg.
            # Only label 0 (person) is allowed to trigger an export.
            if int(label) != PERSON_LABEL or score < self.detection_threshold:
                continue
            x1, y1, x2, y2 = (float(value) for value in box)
            x1, x2 = sorted((min(640.0, max(0.0, x1)), min(640.0, max(0.0, x2))))
            y1, y2 = sorted((min(640.0, max(0.0, y1)), min(640.0, max(0.0, y2))))
            area_ratio = (x2 - x1) * (y2 - y1) / (640.0 * 640.0)
            confidence = max(confidence, score)
            max_area_ratio = max(max_area_ratio, area_ratio)
            count += 1
        return PersonEvidence(confidence, max_area_ratio, count)

    def _person_score(self, image: Image.Image) -> float:
        return self._person_evidence(image).confidence

    def _face_score(self, image: Image.Image) -> float:
        rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        height, width = bgr.shape[:2]
        self.face_detector.setInputSize((width, height))
        _status, faces = self.face_detector.detect(bgr)
        if faces is None or len(faces) == 0:
            return 0.0
        return float(np.max(faces[:, -1]))

    def _evaluate_view(self, image: Image.Image, tiled: bool = False) -> tuple[bool, float, str]:
        face_score = self._face_score(image)
        if face_score >= self.face_threshold:
            prefix = "分块复检" if tiled else ""
            return True, face_score, f"{prefix}检测到人脸或侧脸"

        person = self._person_evidence(image)
        if person.confidence > 0.0:
            prefix = "分块复检" if tiled else ""
            return True, person.confidence, f"{prefix}检测到人物、侧身、背影或人体区域"
        return False, max(face_score, person.confidence), ""

    def detect(self, path: Path) -> DetectionResult:
        started = time.perf_counter()
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            return DetectionResult(True, 1.0, "MP4 按规则直接处理，不进行画面识别", 0.0)
        if suffix not in IMAGE_EXTENSIONS:
            return DetectionResult(False, 0.0, "不支持的图片格式", 0.0)

        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except Exception as exc:
            return DetectionResult(
                False,
                0.0,
                f"图片读取失败：{exc}",
                time.perf_counter() - started,
                error=True,
            )

        passes = 2
        detected, score, reason = self._evaluate_view(image)
        best_score = score
        if detected:
            return DetectionResult(True, score, reason, time.perf_counter() - started, passes)

        if self.enable_tiles:
            for tile in _tiles(image):
                passes += 2
                detected, score, reason = self._evaluate_view(tile, tiled=True)
                best_score = max(best_score, score)
                if detected:
                    return DetectionResult(
                        True, score, reason, time.perf_counter() - started, passes
                    )

        return DetectionResult(
            False,
            best_score,
            "未检测到人脸、侧脸、人物或背影；未导出",
            time.perf_counter() - started,
            passes,
        )
