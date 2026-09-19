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
                radius = max(1,min((min(m.width,m.height)-1)//2,m.strength*2))
                effects = {
                    'blur': f'boxblur=luma_radius={radius}:luma_power=2:chroma_radius=0',
                    'gaussian': f'gblur=sigma={m.strength * 2.4:.1f}:steps=2',
                    'pixelate': f'scale={max(2,m.width//(4+m.strength*2))}:{max(2,m.height//(4+m.strength*2))}:flags=neighbor,scale={m.width}:{m.height}:flags=neighbor',
                    'frosted': f'gblur=sigma={m.strength * 2}:steps=2,eq=brightness=.04:saturation=.7',
                }
                effect = effects.get(m.kind)
                if effect is None: raise ValueError('Kiểu mask không hợp lệ')
                chain = (f'[0:v]format=yuv444p,split[base][region];[region]crop={m.width}:{m.height}:{m.x}:{m.y}:exact=1,'
                         f'{effect}[blur];[base][blur]overlay={m.x}:{m.y}:format=auto[masked];')
            source = '[masked]'
            extra_inputs = []
            for index, logo in enumerate(project.logos, 1):
                opacity = 1 - logo.transparency / 100
                label = f'logo{index}'
                chain += (f'[{index}:v]scale={logo.width}:{logo.height},format=rgba,colorchannelmixer=aa={opacity:.4f},'
                    f'rotate={logo.rotation}*PI/180:ow=rotw(iw):oh=roth(ih):c=none[{label}];'
                    f'{source}[{label}]overlay={logo.x}:{logo.y}:format=auto[base{index}];')
                source = f'[base{index}]'; extra_inputs += ['-loop','1','-i',str(Path(logo.path).resolve())]
            chain += f"{source}ass=filename=subtitle.ass,pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1[v]"
            args = ['ffmpeg','-nostdin','-n']
            if preview: args += ['-ss',str(start)]
            args += ['-i',str(Path(project.source_video_path).resolve())] + extra_inputs
            if preview: args += ['-t',str(duration)]
            args += ['-filter_complex',chain,'-map','[v]','-map','0:a:0?',
                     '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
                     '-c:a','aac','-b:a','192k','-movflags','+faststart']
            if project.logos: args += ['-shortest']
            args += [str(temporary)]
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
