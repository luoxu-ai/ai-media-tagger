# Third-party notices

This repository contains or uses the following third-party projects. Their
respective licenses continue to apply to their code, binaries and model files.

## D-FINE

- Project: https://github.com/Peterande/D-FINE
- License: Apache License 2.0
- Use: the `dfine_m_human_parts_trial.onnx` detector was fine-tuned from a
  D-FINE-M COCO pretrained model.

## YuNet / OpenCV Zoo

- Project: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- License: MIT for the YuNet model directory; see the upstream repository.
- Use: `face_detection_yunet_2023mar.onnx` performs offline face detection.

## OpenCV

- Project: https://opencv.org/
- License: Apache License 2.0 for current OpenCV releases.

## ExifTool

- Project: https://exiftool.org/
- Copyright: Phil Harvey and contributors
- License: Perl Artistic License or GNU GPL; the official package copied by
  `prepare.ps1` includes the complete applicable license text.

## Runtime libraries

The application also depends on PySide6/Qt, ONNX Runtime, NumPy and Pillow.
They are installed from their official Python packages and remain subject to
their own license terms. Dependency versions are declared in `requirements.txt`.

