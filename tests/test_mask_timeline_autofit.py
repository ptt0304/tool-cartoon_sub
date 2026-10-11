import tempfile
import unittest
from pathlib import Path

from cartoon_sub.media.mask_timeline import event_geometry, mask_events_from_ass, mask_filter_chain
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

    def test_autofit_padding_multiline_and_clamp(self):
        project = self.project([])
        one = event_geometry('Ngắn', project)
        multi = event_geometry('Một dòng rất dài\nDòng thứ hai', project)
        self.assertGreater(multi[3], one[3])
        self.assertLessEqual(multi[0] + multi[2], project.mask.x + project.mask.width)
        self.assertLessEqual(multi[1] + multi[3], project.mask.y + project.mask.height)
        self.assertGreaterEqual(one[2], project.mask.padding_x * 2)
        self.assertGreaterEqual(one[3], project.mask.padding_y * 2)

    def test_roi_full_and_mask_types(self):
        project = self.project([Utterance(1, 0, 2, '一', vi='Nội dung')])
        project.mask.geometry_mode = 'roi_full'
        event = self.events(project)[0]
        self.assertEqual((event.x, event.y, event.width, event.height),
                         (project.mask.x, project.mask.y, project.mask.width, project.mask.height))
        self.assertIn('drawbox=', mask_filter_chain(project, [event]))
        project.mask.kind = 'gaussian'
        self.assertIn('gblur=', mask_filter_chain(project, [event]))
        project.mask.kind = 'none'
        self.assertEqual(mask_filter_chain(project, [event]), '[0:v]null[masked];')

    def test_legacy_project_keeps_roi_full(self):
        project = Project.from_dict({'name': 'old', 'source_video_path': 'video.mp4', 'schema_version': 3,
                                     'metadata': {'width': 640, 'height': 360},
                                     'mask': {'enabled': True, 'kind': 'solid', 'x': 10, 'y': 20, 'width': 200, 'height': 40}})
        self.assertEqual(project.mask.geometry_mode, 'roi_full')


if __name__ == '__main__':
    unittest.main()
