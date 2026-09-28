import base64
import binascii
import os

from django.conf import settings


class FaceEngineError(Exception):
    pass


_detector = None
_recognizer = None
_object_detector = None
_object_output_layers = None
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
        0.75,
        0.3,
        5000,
    )
    _recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")
    return _detector, _recognizer


def _get_object_detector():
    global _object_detector, _object_output_layers
    if _object_detector is not None and _object_output_layers is not None:
        return _object_detector, _object_output_layers

    cv2, _ = _libraries()
    model_dir = os.path.join(settings.BASE_DIR, "face_models")
    config_path = os.getenv("OBJECT_DETECTOR_CONFIG", os.path.join(model_dir, "yolov4-tiny.cfg"))
    weights_path = os.getenv("OBJECT_DETECTOR_WEIGHTS", os.path.join(model_dir, "yolov4-tiny.weights"))
    if not os.path.exists(config_path) or not os.path.exists(weights_path):
        raise FaceEngineError("Mobile phone detector model is missing")
    try:
        _object_detector = cv2.dnn.readNetFromDarknet(config_path, weights_path)
        _object_detector.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        _object_detector.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        _object_output_layers = _object_detector.getUnconnectedOutLayersNames()
    except cv2.error as exc:
        raise FaceEngineError("Unable to load mobile phone detector") from exc
    return _object_detector, _object_output_layers


def _detect_mobile_phone(image):
    cv2, np = _libraries()
    detector, output_layers = _get_object_detector()
    height, width = image.shape[:2]
    blob = cv2.dnn.blobFromImage(image, 1 / 255.0, (512, 512), swapRB=True, crop=False)
    detector.setInput(blob)
    boxes = []
    confidences = []
    for output in detector.forward(output_layers):
        for detection in output:
            scores = detection[5:]
            if not len(scores):
                continue
            class_id = int(np.argmax(scores))
            objectness = float(detection[4])
            confidence = objectness * float(scores[class_id])
            if class_id != 67 or confidence < 0.15:
                continue
            center_x, center_y, box_width, box_height = detection[:4]
            pixel_width = int(float(box_width) * width)
            pixel_height = int(float(box_height) * height)
            boxes.append([
                int(float(center_x) * width - pixel_width / 2),
                int(float(center_y) * height - pixel_height / 2),
                pixel_width,
                pixel_height,
            ])
            confidences.append(confidence)
    if not boxes:
        return False, 0.0, []
    indexes = cv2.dnn.NMSBoxes(boxes, confidences, 0.15, 0.4)
    kept = [int(index) for index in np.array(indexes).flatten()] if len(indexes) else []
    detections = [
        {"box": boxes[index], "confidence": round(confidences[index], 3)}
        for index in kept
    ]
    return bool(detections), max((item["confidence"] for item in detections), default=0.0), detections


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


def _detect_faces(image_base64: str):
    image = _decode_image(image_base64)
    detector, _ = _get_engines()
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    return image, faces


def detect_face_count(image_base64: str) -> int:
    _, faces = _detect_faces(image_base64)
    return 0 if faces is None else len(faces)


def analyze_face_posture(image_base64: str):
    image, faces = _detect_faces(image_base64)
    face_count = 0 if faces is None else len(faces)
    phone_detected, phone_confidence, phone_detections = _detect_mobile_phone(image)
    result = {
        "face_count": face_count,
        "face_aligned": face_count == 1,
        "phone_detected": phone_detected,
        "phone_call_suspected": phone_detected,
        "details": {
            "phone_confidence": phone_confidence,
            "phone_detections": phone_detections,
        },
    }
    if face_count != 1:
        result["face_aligned"] = False
        return result

    cv2, _ = _libraries()
    height, width = image.shape[:2]
    face = faces[0]
    x, y, w, h = [float(value) for value in face[:4]]
    landmarks = face[4:14].reshape((5, 2)) if len(face) >= 14 else None
    center_x = (x + w / 2) / max(width, 1)
    center_y = (y + h / 2) / max(height, 1)
    face_width_share = w / max(width, 1)
    face_height_share = h / max(height, 1)
    eye_tilt = 0.0
    nose_offset = 0.0
    if landmarks is not None:
        right_eye, left_eye, nose = landmarks[0], landmarks[1], landmarks[2]
        eye_distance = max(abs(float(left_eye[0] - right_eye[0])), 1.0)
        eye_tilt = abs(float(left_eye[1] - right_eye[1])) / eye_distance
        nose_mid = (float(left_eye[0]) + float(right_eye[0])) / 2
        nose_offset = abs(float(nose[0]) - nose_mid) / eye_distance

    aligned = (
        0.34 <= center_x <= 0.66
        and 0.24 <= center_y <= 0.72
        and face_width_share >= 0.16
        and face_height_share >= 0.20
        and eye_tilt <= 0.18
        and nose_offset <= 0.28
    )

    fx1 = max(0, int(x))
    fy1 = max(0, int(y))
    fx2 = min(width, int(x + w))
    fy2 = min(height, int(y + h))
    side_width = max(4, int(w * 0.22))
    left_roi = image[fy1:fy2, fx1:min(fx1 + side_width, fx2)]
    right_roi = image[fy1:fy2, max(fx2 - side_width, fx1):fx2]
    side_delta = 0.0
    edge_density = 0.0
    if left_roi.size and right_roi.size:
        left_gray = cv2.cvtColor(left_roi, cv2.COLOR_BGR2GRAY)
        right_gray = cv2.cvtColor(right_roi, cv2.COLOR_BGR2GRAY)
        side_delta = abs(float(left_gray.mean()) - float(right_gray.mean()))
        edge_density = max(
            float(cv2.Canny(left_gray, 70, 150).mean()),
            float(cv2.Canny(right_gray, 70, 150).mean()),
        )
    result["face_aligned"] = bool(aligned)
    result["details"].update({
        "center_x": round(float(center_x), 3),
        "center_y": round(float(center_y), 3),
        "face_width_share": round(float(face_width_share), 3),
        "face_height_share": round(float(face_height_share), 3),
        "eye_tilt": round(float(eye_tilt), 3),
        "nose_offset": round(float(nose_offset), 3),
        "side_luminance_delta": round(float(side_delta), 1),
        "side_edge_density": round(float(edge_density), 1),
    })
    return result
