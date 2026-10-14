import tempfile
import unittest
from pathlib import Path

from cartoon_sub.media.mask_timeline import MaskEvent, event_geometry, mask_events_from_ass, mask_filter_chain
from cartoon_sub.subtitle.models import Mask, Project, SubtitleStyle, Utterance
from cartoon_sub.subtitle.renderer import save_ass


class MaskTimelineAutofitTests(unittest.TestCase):
    def project(self, rows):
        return Project('mask-timeline', 'source.mp4', metadata={'width': 640, 'height': 360, 'duration': 20},
                       segments=rows,
                       mask=Mask(True, 'solid', 64, 252, 512, 79, 12, '#000000',
                                 {'x_pct': .1, 'y_pct': .7, 'w_pct': .8, 'h_pct': .22}, 'auto_fit', 18, 8),
                       subtitle_style=SubtitleStyle(font_size=24, outline=2, shadow=1))

    def events(self, project):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'subtitle.ass'
            save_ass(project, path)
            return mask_events_from_ass(path, project)

    def test_no_subtitle_creates_no_mask_chain(self):
        project = self.project([])
        self.assertEqual(mask_filter_chain(project, []), '[0:v]null[masked];')

    def test_timeline_events_preserve_lifetime_gaps_and_back_to_back(self):
        project = self.project([
            Utterance(1, 10, 12, '一', vi='Một'),
            Utterance(2, 13, 15, '二', vi='Hai'),
            Utterance(3, 15, 17, '三', vi='Ba'),
        ])
        events = self.events(project)
        self.assertEqual([(row.start, row.end) for row in events], [(10, 12), (13, 15), (15, 17)])
        chain = mask_filter_chain(project, events)
        self.assertIn("between(t,10.000,11.999)", chain)
        self.assertIn("between(t,13.000,14.999)", chain)
        self.assertNotIn('t=fill[masked]', chain)  # never global

    def test_autofit_padding_multiline_and_frame_clamp(self):
        project = self.project([])
        one = event_geometry('Ngắn', project)
        multi = event_geometry('Một dòng rất dài\nDòng thứ hai', project)
        self.assertGreater(multi[3], one[3])
        self.assertLessEqual(multi[0] + multi[2], project.metadata['width'])
        self.assertLessEqual(multi[1] + multi[3], project.metadata['height'])
        self.assertGreaterEqual(one[2], project.mask.padding_x * 2)
        self.assertGreaterEqual(one[3], project.mask.padding_y * 2)

    def test_mask_types_do_not_change_text_geometry(self):
        project = self.project([Utterance(1, 0, 2, '一', vi='Nội dung')])
        event = self.events(project)[0]
        self.assertNotEqual((event.width, event.height), (project.mask.width, project.mask.height))
        self.assertIn('drawbox=', mask_filter_chain(project, [event]))
        project.mask.kind = 'gaussian'
        gaussian = mask_filter_chain(project, [event])
        self.assertIn('gblur=', gaussian)
        self.assertIn("gblur=sigma=28.8:steps=2:enable='between(t,0.000,1.999)'", gaussian)
        project.mask.kind = 'none'
        self.assertEqual(mask_filter_chain(project, [event]), '[0:v]null[masked];')

    def test_non_overlapping_equal_geometry_reuses_one_gaussian_branch(self):
        project = self.project([])
        project.mask.kind = 'gaussian'
        events = [
            MaskEvent(0, 2, 'Một', 20, 200, 100, 40),
            MaskEvent(3, 5, 'Hai', 20, 200, 100, 40),
        ]
        chain = mask_filter_chain(project, events)
        self.assertEqual(chain.count('gblur='), 1)
        self.assertEqual(chain.count('crop='), 1)
        self.assertEqual(chain.count('overlay='), 1)
        self.assertIn("between(t,0.000,1.999)+between(t,3.000,4.999)", chain)

    def test_overlapping_events_keep_separate_gaussian_branches(self):
        project = self.project([])
        project.mask.kind = 'gaussian'
        events = [
            MaskEvent(0, 3, 'Một', 20, 200, 100, 40),
            MaskEvent(2, 4, 'Hai', 20, 200, 100, 40),
        ]
        chain = mask_filter_chain(project, events)
        self.assertEqual(chain.count('gblur='), 2)
        self.assertEqual(chain.count('overlay='), 2)

    def test_same_center_different_roi_sizes_have_identical_geometry(self):
        project = self.project([])
        project.metadata.update({'width': 1920, 'height': 1080})
        project.mask.x, project.mask.y, project.mask.width, project.mask.height = 800, 900, 320, 100
        first = event_geometry('La Phong, mau đến Thần Điện Cự Phủ!', project)
        project.mask.x, project.mask.y, project.mask.width, project.mask.height = 850, 925, 220, 50
        second = event_geometry('La Phong, mau đến Thần Điện Cự Phủ!', project)
        self.assertEqual(first, second)

    def test_long_short_and_roi_move_follow_center_anchor(self):
        project = self.project([])
        project.metadata.update({'width': 1920, 'height': 1080})
        project.mask.x, project.mask.y, project.mask.width, project.mask.height = 800, 900, 320, 100
        long = event_geometry('La Phong, mau đến Thần Điện Cự Phủ!', project)
        short = event_geometry('Được.', project)
        self.assertGreater(long[2], short[2])
        self.assertAlmostEqual(long[0] + long[2] / 2, project.mask.x + project.mask.width / 2, delta=.5)
        project.mask.x += 100
        moved = event_geometry('La Phong, mau đến Thần Điện Cự Phủ!', project)
        self.assertEqual((moved[0] - long[0], moved[1] - long[1]), (100, 0))
        self.assertEqual(moved[2:], long[2:])

    def test_legacy_project_migrates_to_single_auto_fit_mode(self):
        project = Project.from_dict({'name': 'old', 'source_video_path': 'video.mp4', 'schema_version': 3,
                                     'metadata': {'width': 640, 'height': 360},
                                     'mask': {'enabled': True, 'kind': 'solid', 'x': 10, 'y': 20, 'width': 200, 'height': 40,
                                              'geometry_mode': 'roi_full'}})
        self.assertEqual(project.mask.geometry_mode, 'auto_fit')


if __name__ == '__main__':
    unittest.main()
