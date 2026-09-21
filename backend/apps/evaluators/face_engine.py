import base64
import binascii
import os

from django.conf import settings


class FaceEngineError(Exception):
    pass


_detector = None
_recognizer = None
_cv2 = None
_np = None


def _libraries():
    global _cv2, _np
    if _cv2 is not None and _np is not None:
        return _cv2, _np
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise FaceEngineError("Face recognition dependencies are not installed. Install opencv-contrib-python-headless and numpy, then rebuild the backend environment.") from exc
    _cv2 = cv2
    _np = np
    return _cv2, _np


def _model_paths():
    model_dir = os.path.join(settings.BASE_DIR, "face_models")

    detector_path = os.getenv(
        "FACE_DETECTOR_MODEL",
        os.path.join(model_dir, "face_detection_yunet_2023mar.onnx"),
    )
    recognizer_path = os.getenv(
        "FACE_RECOGNIZER_MODEL",
        os.path.join(model_dir, "face_recognition_sface_2021dec.onnx"),
    )
    return detector_path, recognizer_path


def _get_engines():
    global _detector, _recognizer

    if _detector is not None and _recognizer is not None:
        return _detector, _recognizer

    cv2, _ = _libraries()
    detector_path, recognizer_path = _model_paths()

    if not os.path.exists(detector_path):
        raise FaceEngineError(f"Face detector model missing: {detector_path}")
    if not os.path.exists(recognizer_path):
        raise FaceEngineError(f"Face recognizer model missing: {recognizer_path}")

    _detector = cv2.FaceDetectorYN.create(
        detector_path,
        "",
        (320, 320),
        0.9,
        0.3,
        5000,
    )
    _recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")
    return _detector, _recognizer


def _decode_image(image_base64: str):
    cv2, np = _libraries()
    value = (image_base64 or "").strip()
    if not value:
        raise FaceEngineError("Face image is required")

    if "," in value:
        value = value.split(",", 1)[1]

    try:
        image_bytes = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise FaceEngineError("Invalid face image") from exc

    if len(image_bytes) > 3_000_000:
        raise FaceEngineError("Face image is too large")

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise FaceEngineError("Unable to decode face image")
    return image


def extract_embedding(image_base64: str):
    _, np = _libraries()
    image = _decode_image(image_base64)
    detector, recognizer = _get_engines()

    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)

    if faces is None or len(faces) == 0:
        raise FaceEngineError("No face detected")
    if len(faces) != 1:
        raise FaceEngineError("Exactly one face must be visible")

    aligned_face = recognizer.alignCrop(image, faces[0])
    feature = recognizer.feature(aligned_face)
    embedding = feature.flatten().astype(np.float32)

    norm = np.linalg.norm(embedding)
    if norm == 0:
        raise FaceEngineError("Unable to generate face embedding")

    embedding = embedding / norm
    return [round(float(value), 7) for value in embedding.tolist()]
