from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from person_detector import (
    MIN_PART_AREA_RATIO, PART_DETECTION_THRESHOLD, PersonDetector, _tiles,
)


def detector_for(labels, boxes, scores):
    class FakeSession:
        def run(self, *_args):
            return np.asarray([labels]), np.asarray([boxes]), np.asarray([scores])

    detector = PersonDetector.__new__(PersonDetector)
    detector.session = FakeSession()
    detector.detection_threshold = 0.76
    detector.face_threshold = 0.92
    detector.tiled_face_threshold = 0.95
    detector.part_detection_threshold = PART_DETECTION_THRESHOLD
    detector.min_part_area_ratio = MIN_PART_AREA_RATIO
    detector._face_score = lambda _image: 0.0
    # A strong global classifier must not be able to bypass the limb size limit.
    detector._presence_score = lambda _image: 0.99
    detector._verifier_score = lambda _image: 0.99
    return detector


@pytest.mark.parametrize("label", [1, 2])
def test_visible_limb_without_face_or_person_is_tagged(label):
    detector = detector_for([label], [[100, 100, 260, 220]], [0.95])
    detected, score, reason = detector._evaluate_view(Image.new("RGB", (1000, 1000)))
    assert detected and score == 0.95
    assert ("手部" if label == 1 else "脚部") in reason
    assert "原图面积" in reason


@pytest.mark.parametrize("label", [1, 2])
def test_tiny_limb_is_not_tagged_even_with_strong_classifiers(label):
    detector = detector_for([label], [[100, 100, 110, 110]], [0.99])
    detected, _score, _reason = detector._evaluate_view(Image.new("RGB", (1000, 1000)))
    assert not detected


def test_below_and_above_area_boundary():
    detector = detector_for([1], [[0, 0, 64, 19]], [0.95])
    assert not detector._evaluate_view(Image.new("RGB", (640, 640)))[0]
    detector = detector_for([1], [[0, 0, 64, 20]], [0.95])
    assert detector._evaluate_view(Image.new("RGB", (640, 640)))[0]


def test_high_confidence_tiny_candidate_does_not_hide_valid_larger_candidate():
    detector = detector_for([1, 2], [[0, 0, 8, 8], [100, 100, 300, 250]], [0.99, 0.70])
    evidence = detector._person_evidence(Image.new("RGB", (640, 640)))
    assert evidence.visible_part_confidence == 0.70
    assert evidence.visible_part_label == 2
    assert detector._evaluate_view(Image.new("RGB", (640, 640)))[0]


def test_uncertain_limb_is_not_accepted_just_because_it_is_large():
    detector = detector_for([1], [[0, 0, 640, 640]], [0.55])
    assert not detector._evaluate_view(Image.new("RGB", (1000, 1000)))[0]


def test_tile_enlargement_cannot_bypass_original_image_area_limit():
    original = Image.new("RGB", (1000, 1000))
    tile = next(iter(_tiles(original)))
    # 0.62² * (50 * 50 / 640²) = 0.2346% of original: too small.
    detector = detector_for([1], [[0, 0, 50, 50]], [0.99])
    assert detector._evaluate_view(Image.new("RGB", tile.size), tiled=True)[0]
    assert not detector._evaluate_view(tile, tiled=True)[0]


def test_partial_body_duplicate_is_accepted_as_a_limb():
    detector = detector_for([0, 1], [[50, 60, 200, 200], [50, 60, 200, 200]], [0.80, 0.95])
    evidence = detector._person_evidence(Image.new("RGB", (1000, 1000)))
    assert evidence.candidate_count == 0
    assert detector._evaluate_view(Image.new("RGB", (1000, 1000)))[0]


def test_tiny_hand_does_not_block_a_separate_real_person():
    detector = detector_for([0, 1], [[50, 50, 500, 620], [520, 50, 530, 60]], [0.90, 0.99])
    assert detector._evaluate_view(Image.new("RGB", (1000, 1000)))[0]


def test_tiny_limb_remains_negative_across_full_image_and_tiles(tmp_path: Path):
    image = tmp_path / "tiny.jpg"
    Image.new("RGB", (1000, 1000), "white").save(image)
    detector = detector_for([1], [[0, 0, 10, 10]], [0.99])
    detector._cache = {}
    detector.enable_tiles = True
    result = detector.detect(image)
    assert not result.detected and not result.error
    assert result.passes > 2
    assert "肢体" in result.reason


def test_area_threshold_is_configurable():
    detector = detector_for([1], [[0, 0, 64, 20]], [0.95])
    detector.min_part_area_ratio = 0.01
    assert not detector._evaluate_view(Image.new("RGB", (640, 640)))[0]


def test_global_classifier_requires_spatial_evidence():
    detector = detector_for([1], [[0, 0, 10, 10]], [0.08])
    assert not detector._evaluate_view(Image.new("RGB", (1000, 1000)))[0]


def test_uncertain_but_large_localized_body_can_use_classifier_confirmation():
    detector = detector_for([1], [[50, 50, 500, 500]], [0.45])
    assert detector._evaluate_view(Image.new("RGB", (1000, 1000)))[0]
