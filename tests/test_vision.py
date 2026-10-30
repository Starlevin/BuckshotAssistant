import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from vision import Templates, crop_relative


def shell_scene(color):
    im = Image.new("RGB", (240, 160), "#27353b")
    draw = ImageDraw.Draw(im)
    draw.rectangle((20, 10, 210, 140), outline="white", width=3)
    draw.rectangle((70, 50, 180, 100), fill=color)
    draw.rectangle((160, 50, 180, 100), fill="#ddac44")
    return im


class VisionTests(unittest.TestCase):
    def test_roi_scales_with_client_size(self):
        im = Image.new("RGB", (1920, 1080))
        self.assertEqual(crop_relative(im, [.25, .25, .75, .75]).size, (960, 540))
        for roi in ((0, 0, 2, 1), (0, 0, 0, 1), (1, 1, 0, 0)):
            with self.assertRaises(ValueError):
                crop_relative(im, roi)

    def test_match_and_ambiguous_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            live = shell_scene("#d52c28")
            blank = shell_scene("#5799a8")
            idle = shell_scene("#27353b")
            live.save(Path(directory) / "shot_live--1.png")
            blank.save(Path(directory) / "shot_blank--1.png")
            idle.save(Path(directory) / "idle--1.png")
            templates = Templates(directory)
            self.assertTrue(templates.ready)
            self.assertEqual(templates.classify(live)[0], "shot_live")
            self.assertEqual(templates.classify(blank)[0], "shot_blank")
            self.assertEqual(templates.classify(idle)[0], "idle")
            live.save(Path(directory) / "eject_live--duplicate.png")
            templates.reload()
            self.assertIsNone(templates.classify(live)[0])


if __name__ == "__main__":
    unittest.main()
