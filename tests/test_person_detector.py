from pathlib import Path

import numpy as np
from PIL import Image

from person_detector import (
    PersonDetector,
    _letterbox,
    _presence_input,
    clear_detection_cache_file,
    detection_cache_file_info,
)


def test_letterbox_has_expected_input_shape():
    image = Image.new("RGB", (1200, 400), "white")
    array = _letterbox(image)
    assert array.shape == (1, 3, 640, 640)
    assert array.dtype == np.float32


def test_presence_input_has_expected_shape_and_dtype():
    image = Image.new("RGB", (1200, 400), "white")
    array = _presence_input(image)
    assert array.shape == (1, 3, 224, 224)
    assert array.dtype == np.float32


def test_detection_cache_can_be_inspected_and_cleared_without_loading_models(tmp_path, monkeypatch):
    cache_path = tmp_path / "detection_cache.json"
    cache_path.write_text(
        '{"version":"test","entries":{"one":{},"two":{}}}', encoding="utf-8"
    )
    monkeypatch.setattr("person_detector.default_detection_cache_path", lambda: cache_path)
    count, size = detection_cache_file_info()
    assert count == 2
    assert size > 0
    clear_detection_cache_file()
    assert not cache_path.exists()


def test_presence_verifier_can_confirm_a_person_when_box_models_do_not():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector.presence_threshold = 0.14
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {"confidence": 0.0, "max_area_ratio": 0.0, "candidate_count": 0}
    )()
    detector._presence_score = lambda image: 0.80

    detected, score, reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert detected
    assert score == 0.80
    assert "图像级复检" in reason


def test_second_stage_verifier_rejects_a_weak_image_level_false_positive():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector.presence_threshold = 0.12
    detector.verifier_threshold = 0.025
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {"confidence": 0.0, "max_area_ratio": 0.0, "candidate_count": 0}
    )()
    detector._presence_score = lambda image: 0.80
    detector._verifier_score = lambda image: 0.01

    detected, _score, reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert not detected
    assert reason == ""


def test_second_stage_verifier_keeps_a_reviewed_positive():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector.presence_threshold = 0.12
    detector.verifier_threshold = 0.025
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {"confidence": 0.0, "max_area_ratio": 0.0, "candidate_count": 0}
    )()
    detector._presence_score = lambda image: 0.80
    detector._verifier_score = lambda image: 0.03

    detected, score, reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert detected
    assert score == 0.80
    assert "图像级复检" in reason


def test_uncertain_image_level_result_requires_local_confirmation():
    detector = PersonDetector.__new__(PersonDetector)
    detector.enable_tiles = True
    detector.image_level_local_threshold = 0.14
    detector._presence_score = lambda image: 0.13
    detector._verifier_score = lambda image: 0.63

    confirmed, score = detector._locally_confirms_image_level_person(
        Image.new("RGB", (1000, 1000))
    )

    assert not confirmed
    assert score == 0.13


def test_uncertain_image_level_result_keeps_local_person_evidence():
    detector = PersonDetector.__new__(PersonDetector)
    detector.enable_tiles = True
    detector.image_level_local_threshold = 0.14
    detector._presence_score = lambda image: 0.20
    detector._verifier_score = lambda image: 0.15

    confirmed, score = detector._locally_confirms_image_level_person(
        Image.new("RGB", (1000, 1000))
    )

    assert confirmed
    assert score == 0.15


def test_high_confidence_standalone_hand_vetoes_image_level_fallback():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {
            "confidence": 0.0,
            "max_area_ratio": 0.0,
            "candidate_count": 0,
            "part_confidence": 0.89,
        }
    )()
    detector.presence_threshold = 0.12
    detector.verifier_threshold = 0.268
    detector._presence_score = lambda image: 0.80
    detector._verifier_score = lambda image: 0.30

    detected, score, reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert not detected
    assert score == 0.89
    assert reason == ""


def test_lower_confidence_part_does_not_veto_reviewed_person_fallback():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector.presence_threshold = 0.12
    detector.verifier_threshold = 0.25
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {
            "confidence": 0.0,
            "max_area_ratio": 0.0,
            "candidate_count": 0,
            "part_confidence": 0.77,
        }
    )()
    detector._presence_score = lambda image: 0.80
    detector._verifier_score = lambda image: 0.60

    detected, _score, _reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert detected


def test_strong_verifier_keeps_person_even_with_high_part_confidence():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector.presence_threshold = 0.12
    detector.verifier_threshold = 0.268
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {
            "confidence": 0.0,
            "max_area_ratio": 0.0,
            "candidate_count": 0,
            "part_confidence": 0.89,
        }
    )()
    detector._presence_score = lambda image: 0.80
    detector._verifier_score = lambda image: 0.45

    detected, _score, _reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert detected


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


def test_tiled_face_requires_stricter_confidence_to_avoid_product_false_positive():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.55
    detector.tiled_face_threshold = 0.80
    detector._face_score = lambda image: 0.706
    detector._person_evidence = lambda image: type(
        "Evidence", (), {"confidence": 0.0, "max_area_ratio": 0.0, "candidate_count": 0}
    )()

    detected, score, reason = detector._evaluate_view(
        Image.new("RGB", (640, 640), "white"), tiled=True
    )

    assert not detected
    assert score == 0.706
    assert reason == ""


def test_high_confidence_tiled_face_is_still_accepted():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.55
    detector.tiled_face_threshold = 0.80
    detector._face_score = lambda image: 0.88
    detector._person_evidence = lambda image: (_ for _ in ()).throw(
        AssertionError("person model should not run after a strong tiled face match")
    )

    detected, score, reason = detector._evaluate_view(
        Image.new("RGB", (640, 640), "white"), tiled=True
    )

    assert detected
    assert score == 0.88
    assert reason == "分块复检检测到人脸或侧脸"


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


def test_hand_duplicate_suppresses_person_for_the_same_region():
    class FakeSession:
        def run(self, _outputs, _inputs):
            return (
                np.asarray([[0, 1]], dtype=np.int64),
                np.asarray(
                    [[[200, 250, 420, 390], [200, 250, 420, 390]]],
                    dtype=np.float32,
                ),
                np.asarray([[0.42, 0.95]], dtype=np.float32),
            )

    detector = PersonDetector.__new__(PersonDetector)
    detector.detection_threshold = 0.35
    detector.session = FakeSession()
    evidence = detector._person_evidence(Image.new("RGB", (640, 640), "white"))
    assert evidence.confidence == 0.0
    assert evidence.candidate_count == 0


def test_small_hand_inside_large_person_does_not_suppress_person():
    class FakeSession:
        def run(self, _outputs, _inputs):
            return (
                np.asarray([[0, 1]], dtype=np.int64),
                np.asarray(
                    [[[80, 30, 560, 630], [420, 240, 500, 330]]],
                    dtype=np.float32,
                ),
                np.asarray([[0.78, 0.96]], dtype=np.float32),
            )

    detector = PersonDetector.__new__(PersonDetector)
    detector.detection_threshold = 0.35
    detector.session = FakeSession()
    evidence = detector._person_evidence(Image.new("RGB", (640, 640), "white"))
    assert np.isclose(evidence.confidence, 0.78)
    assert evidence.candidate_count == 1


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


def test_multiple_person_boxes_require_automatic_human_confirmation():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector.verifier_threshold = 0.30
    detector.human_confirmation_threshold = 0.02
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {
            "confidence": 0.85,
            "max_area_ratio": 0.20,
            "candidate_count": 2,
            "part_confidence": 0.0,
        }
    )()
    detector._verifier_score = lambda image: 0.004

    detected, _score, reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert not detected
    assert reason == ""
    assert "真人复检=0.004" in detector._last_stage_details


def test_real_multi_person_scene_passes_the_narrow_fake_person_veto():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector.human_confirmation_threshold = 0.02
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {
            "confidence": 0.95,
            "max_area_ratio": 0.06,
            "candidate_count": 8,
            "part_confidence": 0.0,
        }
    )()
    detector._verifier_score = lambda image: 0.053

    detected, score, reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert detected
    assert score == 0.95
    assert "真人复检确认" in reason


def test_single_person_box_keeps_high_recall_without_extra_rejection():
    detector = PersonDetector.__new__(PersonDetector)
    detector.face_threshold = 0.92
    detector._face_score = lambda image: 0.0
    detector._person_evidence = lambda image: type(
        "Evidence", (), {
            "confidence": 0.82,
            "max_area_ratio": 0.03,
            "candidate_count": 1,
            "part_confidence": 0.0,
        }
    )()
    detector._verifier_score = lambda image: (_ for _ in ()).throw(
        AssertionError("single-person evidence should keep the recall path")
    )

    detected, score, reason = detector._evaluate_view(Image.new("RGB", (640, 640)))

    assert detected
    assert score == 0.82
    assert "人体区域" in reason


def test_detection_cache_survives_a_new_detector_instance(tmp_path):
    source = tmp_path / "缓存测试.jpg"
    Image.new("RGB", (32, 32), "white").save(source)
    cache_path = tmp_path / "detection_cache.json"

    first = PersonDetector.__new__(PersonDetector)
    first.cache_path = cache_path
    first._cache = {}
    first.enable_tiles = False
    first._last_stage_details = "测试阶段=通过"
    first._evaluate_view = lambda image: (True, 0.91, "测试检测通过")
    original = first.detect(source)
    first.flush_cache()

    second = PersonDetector.__new__(PersonDetector)
    second.cache_path = cache_path
    second._cache = second._load_cache()
    cached = second.detect(source)

    assert original.detected
    assert cached.detected
    assert cached.cached
    assert cached.elapsed_seconds == 0.0
    assert cached.details == "测试阶段=通过"
