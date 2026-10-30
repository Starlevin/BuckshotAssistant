import unittest
import numpy as np
from PIL import Image
from upstream_vision import preprocess, decode


class VisionAdapterTests(unittest.TestCase):
    def test_rgb_letterbox_retains_coordinates(self):
        tensor, scale, left, top = preprocess(Image.new("RGB", (640, 360), (255, 0, 0)))
        self.assertEqual(tensor.shape, (1, 3, 1280, 1280))
        self.assertEqual((scale, left, top), (2, 0, 280))
        self.assertEqual(float(tensor[0, 0, 640, 640]), 1)
        self.assertEqual(float(tensor[0, 2, 640, 640]), 0)

    def test_class_nms_and_original_coordinate_conversion(self):
        raw = np.zeros((1, 17, 3), dtype=np.float32)
        raw[0, :4, :] = np.array([[640, 640, 640], [640, 640, 640], [200, 200, 200], [100, 100, 100]])
        raw[0, 4+12, 0] = .9
        raw[0, 4+12, 1] = .8
        raw[0, 4+11, 2] = .85
        decoded = decode(raw, 2, 0, 280, 640, 360)
        self.assertEqual(len(decoded), 2)
        self.assertEqual({x['label'] for x in decoded}, {'shell_live', 'shell_blank'})
        self.assertEqual(decoded[0]['box'], [270, 155, 370, 205])

    def test_empty_detections_remain_empty_candidates(self):
        raw = np.zeros((1, 17, 2), dtype=np.float32)
        self.assertEqual(decode(raw, 1, 0, 0, 1280, 1280), [])
