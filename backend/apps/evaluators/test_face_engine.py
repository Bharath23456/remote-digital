from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from apps.evaluators.face_engine import extract_embedding


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


class FaceEngineTests(SimpleTestCase):
    @patch("apps.evaluators.face_engine._get_engines", return_value=(_Detector(), _Recognizer()))
    @patch("apps.evaluators.face_engine._decode_image", return_value=np.zeros((480, 640, 3), dtype=np.uint8))
    def test_extract_embedding_normalises_recognizer_output(self, _decode_image, _get_engines):
        self.assertEqual(extract_embedding("image"), [0.6, 0.8])

