# Model card

## Included models

### `dfine_m_human_parts_trial.onnx`

- Purpose: high-recall offline detection of people in ecommerce media.
- Base model: D-FINE-M COCO pretrained weights.
- Fine-tuning: a small, manually reviewed private ecommerce image dataset.
- Output used by the application: the `person` class. Hand/arm and foot/leg
  classes are ignored by the current application logic.
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

