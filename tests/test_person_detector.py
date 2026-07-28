from pathlib import Path

import numpy as np
from PIL import Image

from person_detector import PersonDetector, _letterbox


def test_letterbox_has_expected_input_shape():
    image = Image.new("RGB", (1200, 400), "white")
    array = _letterbox(image)
    assert array.shape == (1, 3, 640, 640)
    assert array.dtype == np.float32


def test_mp4_is_directly_accepted_without_model_inference(tmp_path):
    detector = PersonDetector.__new__(PersonDetector)
    result = detector.detect(tmp_path / "中文视频.mp4")
    assert result.detected
    assert result.confidence == 1.0
    assert "直接处理" in result.reason


def test_face_or_profile_is_checked_before_person():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.55
    detector._face_score = lambda image: 0.90
    detector._person_evidence = lambda image: (_ for _ in ()).throw(
        AssertionError("person model should not run after a face match")
    )
    detected, score, reason = detector._evaluate_view(Image.new("RGB", (100, 100)))
    assert detected
    assert score == 0.90
    assert "人脸或侧脸" in reason


def test_unreadable_image_is_reported_as_error(tmp_path):
    source = tmp_path / "损坏图片.jpg"
    source.write_bytes(b"not an image")
    detector = PersonDetector.__new__(PersonDetector)
    result = detector.detect(source)
    assert not result.detected
    assert result.error
    assert "图片读取失败" in result.reason


def test_hands_and_feet_never_trigger_even_when_large():
    class FakeSession:
        def __init__(self, label):
            self.label = label

        def run(self, _outputs, _inputs):
            return (
                np.asarray([[self.label]], dtype=np.int64),
                np.asarray([[[0, 0, 640, 640]]], dtype=np.float32),
                np.asarray([[0.99]], dtype=np.float32),
            )

    detector = PersonDetector.__new__(PersonDetector)
    detector.detection_threshold = 0.35
    image = Image.new("RGB", (640, 640), "white")
    detector.session = FakeSession(1)
    assert detector._person_score(image) == 0.0
    detector.session = FakeSession(2)
    assert detector._person_score(image) == 0.0


def test_person_is_accepted_at_high_recall_threshold():
    class FakeSession:
        def run(self, _outputs, _inputs):
            return (
                np.asarray([[0]], dtype=np.int64),
                np.asarray([[[100, 100, 200, 300]]], dtype=np.float32),
                np.asarray([[0.36]], dtype=np.float32),
            )

    detector = PersonDetector.__new__(PersonDetector)
    detector.detection_threshold = 0.35
    detector.session = FakeSession()
    evidence = detector._person_evidence(Image.new("RGB", (640, 640), "white"))
    assert np.isclose(evidence.confidence, 0.36)


def test_any_person_area_is_accepted_without_area_limit():
    class Evidence:
        confidence = 0.80
        max_area_ratio = 0.001

    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.55
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: Evidence()
    detected, _score, reason = detector._evaluate_view(Image.new("RGB", (100, 100)))
    assert detected
    assert "人体区域" in reason
