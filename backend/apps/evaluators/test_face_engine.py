from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from apps.evaluators.face_engine import _detect_mobile_phone, detect_face_count, extract_embedding


class _Detector:
    def setInputSize(self, size):
        self.size = size

    def detect(self, image):
        return None, np.zeros((1, 15), dtype=np.float32)


class _Recognizer:
    def alignCrop(self, image, face):
        return image

    def feature(self, image):
        return np.array([[3.0, 4.0]], dtype=np.float32)


class _ObjectDetector:
    def setInput(self, blob):
        self.blob = blob

    def forward(self, output_layers):
        detection = np.zeros(85, dtype=np.float32)
        detection[:5] = [0.5, 0.5, 0.2, 0.4, 0.9]
        detection[5 + 67] = 0.9
        return [np.array([detection])]


class _LowConfidencePhoneDetector(_ObjectDetector):
    def forward(self, output_layers):
        detection = np.zeros(85, dtype=np.float32)
        detection[:5] = [0.5, 0.5, 0.2, 0.4, 0.4]
        detection[5 + 67] = 0.4
        return [np.array([detection])]


class FaceEngineTests(SimpleTestCase):
    @patch("apps.evaluators.face_engine._get_engines", return_value=(_Detector(), _Recognizer()))
    @patch("apps.evaluators.face_engine._decode_image", return_value=np.zeros((480, 640, 3), dtype=np.uint8))
    def test_extract_embedding_normalises_recognizer_output(self, _decode_image, _get_engines):
        self.assertEqual(extract_embedding("image"), [0.6, 0.8])

    @patch("apps.evaluators.face_engine._get_engines", return_value=(_Detector(), _Recognizer()))
    @patch("apps.evaluators.face_engine._decode_image", return_value=np.zeros((480, 640, 3), dtype=np.uint8))
    def test_detect_face_count_uses_detector_result(self, _decode_image, _get_engines):
        self.assertEqual(detect_face_count("image"), 1)

    @patch("apps.evaluators.face_engine._get_object_detector", return_value=(_ObjectDetector(), ["output"]))
    def test_mobile_phone_detector_reads_coco_cell_phone_class(self, _get_object_detector):
        detected, confidence, detections = _detect_mobile_phone(np.zeros((240, 320, 3), dtype=np.uint8))
        self.assertTrue(detected)
        self.assertGreater(confidence, 0.8)
        self.assertEqual(len(detections), 1)

    @patch("apps.evaluators.face_engine._get_object_detector", return_value=(_LowConfidencePhoneDetector(), ["output"]))
    def test_mobile_phone_detector_keeps_two_frame_candidate_threshold(self, _get_object_detector):
        detected, confidence, detections = _detect_mobile_phone(np.zeros((480, 640, 3), dtype=np.uint8))
        self.assertTrue(detected)
        self.assertGreater(confidence, 0.12)
        self.assertEqual(len(detections), 1)

