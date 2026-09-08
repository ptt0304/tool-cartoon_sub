"""Local frame/preview/final rendering, isolated output per run."""
from pathlib import Path
from uuid import uuid4
from cartoon_sub.media.process import run_process
from cartoon_sub.subtitle.renderer import save_ass, validate_visuals
from cartoon_sub.project.cache import atomic_json, check_cancel


class VideoRenderer:
    def frame(self, project, directory, start=0, cancel=None, progress=None):
        self.validate_time(project, start)
        folder = Path(directory).resolve() / 'preview'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'frame_{uuid4().hex}.png'
        try:
            run_process(['ffmpeg','-nostdin','-n','-ss',str(start),'-i',str(Path(project.source_video_path).resolve()),
                '-frames:v','1','-update','1',str(target)],cancel,progress)
            if not target.is_file(): raise ValueError('Không lấy được khung hình tại mốc đã chọn')
            return target
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @staticmethod
    def validate_time(project, start):
        import math
        if not math.isfinite(start) or not 0 <= start < float(project.metadata['duration']):
            raise ValueError('Mốc xem thử phải nằm trong thời lượng video')
        if not Path(project.source_video_path).is_file():
            raise ValueError('Không tìm thấy video nguồn')

    def render(self, project, directory, start=0, preview=True, cancel=None, progress=None):
        self.validate_time(project, start if preview else 0)
        validate_visuals(project)
        folder = Path(directory).resolve() / ('preview' if preview else 'output') / uuid4().hex
        folder.mkdir(parents=True)
        target = folder / ('preview.mp4' if preview else 'final.mp4')
        temporary = folder / 'rendering.mp4'
        duration = min(10., float(project.metadata['duration'])-start) if preview else None
        state = {'status':'running','start':start if preview else 0,'duration':duration,
                 'mask':vars(project.mask),'subtitle_style':vars(project.subtitle_style)}
        atomic_json(folder/'render.json',state)
        try:
            save_ass(project,folder/'subtitle.ass',start if preview else 0,duration)
            m = project.mask
            if not m.enabled: chain = '[0:v]null[masked];'
            elif m.kind == 'solid':
                chain = f'[0:v]drawbox=x={m.x}:y={m.y}:w={m.width}:h={m.height}:color=black:t=fill[masked];'
            else:
                # RGB avoids chroma rounding changing the user's selected rectangle.
                radius = max(1,min(12,(min(m.width,m.height)-1)//2))
                chain = (f'[0:v]format=yuv444p,split[base][region];[region]crop={m.width}:{m.height}:{m.x}:{m.y}:exact=1,'
                         f'boxblur=luma_radius={radius}:luma_power=2:chroma_radius=0[blur];'
                         f'[base][blur]overlay={m.x}:{m.y}:format=auto[masked];')
            chain += "[masked]ass=filename=subtitle.ass,pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1[v]"
            args = ['ffmpeg','-nostdin','-n']
            if preview: args += ['-ss',str(start)]
            args += ['-i',str(Path(project.source_video_path).resolve())]
            if preview: args += ['-t',str(duration)]
            args += ['-filter_complex',chain,'-map','[v]','-map','0:a:0?',
                     '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
                     '-c:a','aac','-b:a','192k','-movflags','+faststart',str(temporary)]
            if progress: progress('FFmpeg đang render preview 10 giây…' if preview else 'FFmpeg đang render toàn bộ video…')
            run_process(args,cancel,progress,cwd=folder)
            check_cancel(cancel)
            temporary.replace(target)
            state.update(status='completed',output=str(target))
            atomic_json(folder/'render.json',state)
            return target
        except Exception:
            temporary.unlink(missing_ok=True)
            state['status']='failed_or_cancelled'
            atomic_json(folder/'render.json',state)
            raise
