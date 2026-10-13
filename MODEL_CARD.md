# Model card

## Included models

### `dfine_m_human_parts_trial.onnx`

- Purpose: high-recall offline detection of people in ecommerce media.
- Base model: D-FINE-M COCO pretrained weights.
- Fine-tuning: a small, manually reviewed private ecommerce image dataset.
- Output used by the application: the `person`, hand/arm and foot/leg classes.
  From 2026-10-13, clearly detected standalone limbs also qualify for tagging.
  Direct limb acceptance requires confidence >= 0.60 and a bounding box area
  >= 0.3% of the original image. These thresholds are configurable.
  Overlapping crop detections are measured against the original image area,
  rather than the enlarged crop. Tiny localized limbs cannot bypass the area
  limit through image-level classification.
  Image-level fallback requires spatial evidence at confidence >= 0.30 and
  the same minimum original-image area; a classifier score alone is insufficient.
  The thresholds concern detection boxes, not exact exposed-skin pixel counts.
- License for this published fine-tuned weight: Apache License 2.0.

### `face_detection_yunet_2023mar.onnx`

- Purpose: offline face and profile-face detection.
- Source: OpenCV Zoo YuNet.
- Upstream license: MIT for files in the YuNet model directory.

## Limitations

The detectors can produce false positives and false negatives. Results depend
on image scale, crop, occlusion, illustration style and image quality. Detection
does not determine whether a person is synthetic and must not be treated as a
legal or marketplace compliance decision. Users must review exported media.

The models do not identify people and do not generate biometric embeddings.
All inference runs locally.

