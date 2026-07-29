from __future__ import annotations

import os
import shutil
import time
import ctypes
import json
import hashlib
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
PART_LABELS = {1, 2}
PART_SUPPRESSION_THRESHOLD = 0.50
PART_SUPPRESSION_IOU = 0.55
STANDALONE_PART_VETO_THRESHOLD = 0.85
STANDALONE_PART_VERIFIER_MAX = 0.40
MULTI_PERSON_CONFIRMATION_COUNT = 2
DETECTION_CACHE_VERSION = "20260729-small-person-v2"
PRESENCE_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
PRESENCE_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass(frozen=True)
class DetectionResult:
    detected: bool
    confidence: float
    reason: str
    elapsed_seconds: float
    passes: int = 1
    error: bool = False
    details: str = ""
    cached: bool = False


@dataclass(frozen=True)
class PersonEvidence:
    confidence: float = 0.0
    max_area_ratio: float = 0.0
    candidate_count: int = 0
    part_confidence: float = 0.0


class ModelUnavailableError(RuntimeError):
    pass


def _clamp_box(box: np.ndarray) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(value) for value in box)
    x1, x2 = sorted((min(640.0, max(0.0, x1)), min(640.0, max(0.0, x2))))
    y1, y2 = sorted((min(640.0, max(0.0, y1)), min(640.0, max(0.0, y2))))
    return x1, y1, x2, y2


def _box_area(box: tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _box_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = _box_area(first) + _box_area(second) - intersection
    return intersection / union if union > 0.0 else 0.0


def _windows_short_path(path: Path) -> Path:
    """Return an existing path's 8.3 alias when Windows provides one."""
    if os.name != "nt":
        return path
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(
        str(path), buffer, len(buffer)
    )
    if 0 < length < len(buffer):
        return Path(buffer.value)
    return path


def _opencv_cache_roots() -> list[Path]:
    roots: list[Path] = []
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        roots.append(Path(temp))
    public = os.environ.get("PUBLIC")
    if public:
        roots.append(Path(public) / "Documents")
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        roots.append(Path(program_data))
    system_drive = os.environ.get("SystemDrive", "C:")
    roots.append(Path(f"{system_drive}\\Users\\Public\\Documents"))
    return roots


def _opencv_safe_model_path(source: Path) -> Path:
    """Copy a model to a verified ASCII path for OpenCV on Unicode profiles."""
    failures: list[str] = []
    for root in _opencv_cache_roots():
        cache = root / "ai_media_tagger_models"
        target = cache / source.name
        temporary = cache / f"{source.name}.tmp"
        try:
            cache.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            safe_target = _windows_short_path(target)
            if str(safe_target).isascii():
                return safe_target
            failures.append(f"{target}（无法转换为英文短路径）")
        except OSError as exc:
            failures.append(f"{target}（{exc}）")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    raise ModelUnavailableError(
        "无法为 OpenCV 创建英文模型缓存路径：" + "；".join(failures)
    )


def _letterbox(image: Image.Image, size: int = 640) -> np.ndarray:
    """Resize exactly as the custom D-FINE training pipeline does."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    resized = image.resize((size, size), Image.Resampling.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    return np.transpose(array, (2, 0, 1))[None, ...]


def _presence_input(image: Image.Image) -> np.ndarray:
    """Match torchvision's EfficientNet-B0 inference preprocessing."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    scale = 256.0 / min(width, height)
    resized_width = max(256, round(width * scale))
    resized_height = max(256, round(height * scale))
    image = image.resize((resized_width, resized_height), Image.Resampling.BICUBIC)
    left = (resized_width - 224) // 2
    top = (resized_height - 224) // 2
    image = image.crop((left, top, left + 224, top + 224))
    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - PRESENCE_MEAN) / PRESENCE_STD
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
    """Offline face/person detector with an image-level false-positive verifier."""

    def __init__(
        self,
        detection_threshold: float = 0.76,
        face_threshold: float = 0.92,
        tiled_face_threshold: float = 0.95,
        presence_threshold: float = 0.12,
        verifier_threshold: float = 0.35,
        human_confirmation_threshold: float = 0.02,
        enable_tiles: bool = True,
        tile_trigger_threshold: float = 0.08,
        cache_path: Path | None = None,
    ) -> None:
        self.detection_threshold = detection_threshold
        self.face_threshold = face_threshold
        self.tiled_face_threshold = max(face_threshold, tiled_face_threshold)
        self.presence_threshold = presence_threshold
        self.verifier_threshold = verifier_threshold
        self.human_confirmation_threshold = human_confirmation_threshold
        self.enable_tiles = enable_tiles
        self.tile_trigger_threshold = tile_trigger_threshold
        detector_path = bundled_path("models/dfine_m_human_parts_trial.onnx")
        face_path = bundled_path("models/face_detection_yunet_2023mar.onnx")
        presence_path = bundled_path("models/presence_classifier_efficientnet_b0.onnx")
        verifier_path = bundled_path("models/presence_classifier_verifier_final.onnx")
        self.cache_version = (
            f"{DETECTION_CACHE_VERSION}:"
            f"{hashlib.sha256(verifier_path.read_bytes()).hexdigest()[:16]}:"
            f"{self.detection_threshold:.3f}:{self.face_threshold:.3f}:"
            f"{self.verifier_threshold:.3f}:{self.human_confirmation_threshold:.3f}:"
            f"{self.tile_trigger_threshold:.3f}"
        ) if verifier_path.is_file() else DETECTION_CACHE_VERSION
        self.cache_path = cache_path or self._default_cache_path()
        self._cache: dict[str, dict] = self._load_cache()
        self._cache_dirty = 0
        self._last_stage_details = ""
        missing = [
            str(path)
            for path in (detector_path, face_path, presence_path, verifier_path)
            if not path.is_file()
        ]
        if missing:
            raise ModelUnavailableError("缺少人物识别模型：" + "；".join(missing))

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = 0
        try:
            self.session = ort.InferenceSession(
                str(detector_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.presence_session = ort.InferenceSession(
                str(presence_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.verifier_session = ort.InferenceSession(
                str(verifier_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            try:
                model_buffer = np.frombuffer(face_path.read_bytes(), dtype=np.uint8)
                config_buffer = np.empty(0, dtype=np.uint8)
                self.face_detector = cv2.FaceDetectorYN.create(
                    "onnx",
                    model_buffer,
                    config_buffer,
                    (320, 320),
                    self.face_threshold,
                    0.3,
                    5000,
                )
            except Exception:
                safe_face_path = _opencv_safe_model_path(face_path)
                self.face_detector = cv2.FaceDetectorYN.create(
                    str(safe_face_path), "", (320, 320), self.face_threshold, 0.3, 5000
                )
        except Exception as exc:
            raise ModelUnavailableError(f"人物识别模型加载失败：{exc}") from exc

    @staticmethod
    def _default_cache_path() -> Path:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "AI媒体标签工具" / "detection_cache.json"

    def _load_cache(self) -> dict[str, dict]:
        path = getattr(self, "cache_path", None)
        if path is None:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != getattr(
                self, "cache_version", DETECTION_CACHE_VERSION
            ):
                return {}
            entries = payload.get("entries", {})
            return entries if isinstance(entries, dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    @staticmethod
    def _cache_key(path: Path) -> str | None:
        try:
            stat = path.stat()
            resolved = str(path.resolve()).casefold()
            return f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            return None

    def _cached_result(self, path: Path) -> DetectionResult | None:
        key = self._cache_key(path)
        if key is None:
            return None
        record = getattr(self, "_cache", {}).get(key)
        if not isinstance(record, dict):
            return None
        try:
            return DetectionResult(
                bool(record["detected"]),
                float(record["confidence"]),
                str(record["reason"]),
                0.0,
                int(record.get("passes", 0)),
                bool(record.get("error", False)),
                str(record.get("details", "")),
                True,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _store_cached_result(self, path: Path, result: DetectionResult) -> None:
        if result.error:
            return
        key = self._cache_key(path)
        cache = getattr(self, "_cache", None)
        cache_path = getattr(self, "cache_path", None)
        if key is None or cache is None or cache_path is None:
            return
        cache[key] = {
            "detected": result.detected,
            "confidence": result.confidence,
            "reason": result.reason,
            "passes": result.passes,
            "error": result.error,
            "details": result.details,
            "saved_at": int(time.time()),
        }
        self._cache_dirty = getattr(self, "_cache_dirty", 0) + 1
        # Keep the cache bounded; the oldest entries are least useful.
        if len(cache) > 20000:
            oldest = sorted(
                cache, key=lambda item: int(cache[item].get("saved_at", 0))
            )[: len(cache) - 18000]
            for item in oldest:
                cache.pop(item, None)
        if self._cache_dirty >= 25:
            self.flush_cache()

    def flush_cache(self) -> None:
        cache = getattr(self, "_cache", None)
        cache_path = getattr(self, "cache_path", None)
        if not cache or cache_path is None or getattr(self, "_cache_dirty", 0) <= 0:
            return
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "version": getattr(
                            self, "cache_version", DETECTION_CACHE_VERSION
                        ),
                        "entries": cache,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, cache_path)
            self._cache_dirty = 0
        except OSError:
            pass

    def _person_evidence(self, image: Image.Image) -> PersonEvidence:
        input_data = _letterbox(image)
        size = np.asarray([[640, 640]], dtype=np.int64)
        labels, boxes, scores = self.session.run(
            None, {"images": input_data, "orig_target_sizes": size}
        )
        labels = np.asarray(labels).reshape(-1)
        boxes = np.asarray(boxes).reshape(-1, 4)
        scores = np.asarray(scores).reshape(-1)
        person_candidates: list[
            tuple[float, tuple[float, float, float, float]]
        ] = []
        part_candidates: list[
            tuple[float, tuple[float, float, float, float]]
        ] = []
        for label, box, score in zip(labels, boxes, scores):
            score = float(score)
            label = int(label)
            normalized_box = _clamp_box(box)
            if label == PERSON_LABEL and score >= self.detection_threshold:
                person_candidates.append((score, normalized_box))
            elif label in PART_LABELS and score >= PART_SUPPRESSION_THRESHOLD:
                part_candidates.append((score, normalized_box))

        confidence = 0.0
        max_area_ratio = 0.0
        count = 0
        for score, box in person_candidates:
            is_body_part_duplicate = any(
                part_score > score
                and _box_iou(box, part_box) >= PART_SUPPRESSION_IOU
                for part_score, part_box in part_candidates
            )
            if is_body_part_duplicate:
                continue
            area_ratio = _box_area(box) / (640.0 * 640.0)
            confidence = max(confidence, score)
            max_area_ratio = max(max_area_ratio, area_ratio)
            count += 1
        part_confidence = max((score for score, _box in part_candidates), default=0.0)
        return PersonEvidence(confidence, max_area_ratio, count, part_confidence)

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

    def _presence_score(self, image: Image.Image) -> float:
        session = getattr(self, "presence_session", None)
        if session is None:
            return 0.0
        logits = np.asarray(
            session.run(None, {"images": _presence_input(image)})[0], dtype=np.float64
        ).reshape(-1)
        logits -= np.max(logits)
        probabilities = np.exp(logits)
        return float(probabilities[1] / probabilities.sum())

    def _verifier_score(self, image: Image.Image) -> float:
        session = getattr(self, "verifier_session", None)
        if session is None:
            return 1.0
        logits = np.asarray(
            session.run(None, {"images": _presence_input(image)})[0], dtype=np.float64
        ).reshape(-1)
        logits -= np.max(logits)
        probabilities = np.exp(logits)
        return float(probabilities[1] / probabilities.sum())

    def _evaluate_view(self, image: Image.Image, tiled: bool = False) -> tuple[bool, float, str]:
        face_score = self._face_score(image)
        required_face_score = self.tiled_face_threshold if tiled else self.face_threshold
        if face_score >= required_face_score:
            self._last_stage_details = (
                f"人脸={face_score:.3f}（通过）；人物框=未执行；图像初筛=未执行；复检=未执行"
            )
            prefix = "分块复检" if tiled else ""
            return True, face_score, f"{prefix}检测到人脸或侧脸"

        person = self._person_evidence(image)
        if person.confidence > 0.0:
            if (
                not tiled
                and getattr(person, "candidate_count", 0) >= MULTI_PERSON_CONFIRMATION_COUNT
            ):
                verifier_score = self._verifier_score(image)
                self._last_stage_details = (
                    f"人脸={face_score:.3f}；人物框={person.confidence:.3f}"
                    f"（{getattr(person, 'candidate_count', 0)}个候选）；真人复检={verifier_score:.3f}"
                )
                if verifier_score < getattr(
                    self, "human_confirmation_threshold", 0.02
                ):
                    return False, max(person.confidence, verifier_score), ""
                return (
                    True,
                    person.confidence,
                    "多人物候选经自动真人复检确认",
                )
            self._last_stage_details = (
                f"人脸={face_score:.3f}；人物框={person.confidence:.3f}"
                f"（{getattr(person, 'candidate_count', 0)}个候选，通过）；图像复检=无需执行"
            )
            prefix = "分块复检" if tiled else ""
            return True, person.confidence, f"{prefix}检测到人物、侧身、背影或人体区域"
        presence_score = 0.0 if tiled else self._presence_score(image)
        if presence_score >= getattr(self, "presence_threshold", 0.12):
            verifier_score = self._verifier_score(image)
            self._last_stage_details = (
                f"人脸={face_score:.3f}；人物框=0.000；图像初筛={presence_score:.3f}；"
                f"复检={verifier_score:.3f}"
            )
            if (
                getattr(person, "part_confidence", 0.0)
                >= STANDALONE_PART_VETO_THRESHOLD
                and verifier_score < STANDALONE_PART_VERIFIER_MAX
            ):
                return False, max(person.part_confidence, presence_score), ""
            if verifier_score >= getattr(self, "verifier_threshold", 0.35):
                return True, presence_score, "图像级复检确认包含人物或大面积人体区域"
        else:
            self._last_stage_details = (
                f"人脸={face_score:.3f}；人物框=0.000；图像初筛={presence_score:.3f}（未通过）"
            )
        return False, max(face_score, person.confidence, presence_score), ""

    def detect(self, path: Path) -> DetectionResult:
        started = time.perf_counter()
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            return DetectionResult(True, 1.0, "MP4 按规则直接处理，不进行画面识别", 0.0)
        if suffix not in IMAGE_EXTENSIONS:
            return DetectionResult(False, 0.0, "不支持的图片格式", 0.0)

        cached = self._cached_result(path)
        if cached is not None:
            return cached

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
            result = DetectionResult(
                True, score, reason, time.perf_counter() - started, passes,
                details=getattr(self, "_last_stage_details", ""),
            )
            self._store_cached_result(path, result)
            return result

        if self.enable_tiles and best_score >= getattr(
            self, "tile_trigger_threshold", 0.08
        ):
            for tile in _tiles(image):
                passes += 2
                detected, score, reason = self._evaluate_view(tile, tiled=True)
                best_score = max(best_score, score)
                if detected:
                    result = DetectionResult(
                        True, score, reason, time.perf_counter() - started, passes,
                        details=getattr(self, "_last_stage_details", ""),
                    )
                    self._store_cached_result(path, result)
                    return result

        result = DetectionResult(
            False,
            best_score,
            "未检测到人脸、侧脸、人物或背影；未导出",
            time.perf_counter() - started,
            passes,
            details=getattr(self, "_last_stage_details", ""),
        )
        self._store_cached_result(path, result)
        return result
