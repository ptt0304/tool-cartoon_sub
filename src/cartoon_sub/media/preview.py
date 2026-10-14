"""Local frame/preview/final rendering, isolated output per run."""
import wave
import os
import shutil
import logging
import subprocess
from pathlib import Path
from uuid import uuid4
from cartoon_sub.media.process import run_process
from cartoon_sub.subtitle.renderer import save_ass, validate_visuals
from cartoon_sub.media.mask_timeline import mask_events_from_ass, mask_filter_chain
from cartoon_sub.project.cache import atomic_json, check_cancel


log = logging.getLogger(__name__)


def _argv_length(args):
    """Measure the Windows command line that subprocess will pass to CreateProcess."""
    return len(subprocess.list2cmdline([str(value) for value in args]))


def _write_filter_script(folder, chain):
    """Keep a generated filter graph out of the Windows process command line."""
    path = Path(folder) / "fc.txt"
    path.write_text(chain, encoding="utf-8", newline="\n")
    return path


def validate_final_audio(project, directory) -> Path:
    root = Path(directory).resolve()
    final_audio = root / 'audio' / 'final_audio.wav'
    if not final_audio.is_file():
        raise ValueError("FINAL_AUDIO_NOT_FOUND: Chưa tạo file audio/final_audio.wav. Hãy chuyển sang tab Audio và bấm 'Build Final Audio'.")
    if getattr(project, 'final_audio_status', '') == 'stale':
        raise ValueError("FINAL_AUDIO_STALE: Cấu hình audio đã thay đổi. Cần tạo lại Final Audio trước khi export.")
    try:
        if final_audio.stat().st_size <= 44:
            raise ValueError("FINAL_AUDIO_INVALID: File audio/final_audio.wav rỗng hoặc không hợp lệ")
        with wave.open(str(final_audio), 'rb') as reader:
            if reader.getnchannels() <= 0 or reader.getframerate() <= 0 or reader.getnframes() <= 0:
                raise ValueError("FINAL_AUDIO_INVALID: File audio/final_audio.wav không hợp lệ")
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"FINAL_AUDIO_INVALID: File audio/final_audio.wav không hợp lệ ({exc})") from exc
    return final_audio


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
        if not math.isfinite(start) or not 0 <= start <= float(project.metadata['duration']):
            raise ValueError('Mốc thời gian phải nằm trong thời lượng video')
        if not Path(project.source_video_path).is_file():
            raise ValueError('Không tìm thấy video nguồn')

    def render(self, project, directory, start=0, preview=True, duration=None, use_final_audio=False,
               export_name=None, cancel=None, progress=None):
        self.validate_time(project, start if (preview or duration is not None) else 0)
        validate_visuals(project)
        is_partial = preview or (duration is not None)
        root = Path(directory).resolve()
        export_job = bool(export_name)
        folder = (root / '.tmp' / 'export' / uuid4().hex) if export_job else root / ('preview' if preview else 'output') / uuid4().hex
        folder.mkdir(parents=True)
        target = folder / ('preview.mp4' if preview else ('test_30s.mp4' if duration is not None else 'final.mp4'))
        destination = root / export_name if export_job else target
        temporary = folder / 'rendering.mp4'
        filter_script = None

        if duration is not None:
            calc_duration = max(0.0, min(float(duration), float(project.metadata['duration']) - float(start)))
        elif preview:
            calc_duration = max(0.0, min(10., float(project.metadata['duration']) - float(start)))
        else:
            calc_duration = None

        state = {'status':'running','start':start if is_partial else 0,'duration':calc_duration,
                 'mask':vars(project.mask),'subtitle_style':vars(project.subtitle_style)}
        atomic_json(folder/'render.json',state)
        try:
            ass_path = folder / 'subtitle.ass'
            save_ass(project, ass_path, start if is_partial else 0, calc_duration)
            mask_events = mask_events_from_ass(ass_path, project)
            chain = mask_filter_chain(project, mask_events)
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
            filter_script = _write_filter_script(folder, chain)

            args = ['ffmpeg','-nostdin','-n']
            if is_partial: args += ['-ss',str(start)]
            args += ['-i',str(Path(project.source_video_path).resolve())] + extra_inputs
            if calc_duration is not None: args += ['-t',str(calc_duration)]

            if use_final_audio:
                final_audio = validate_final_audio(project, directory)
                audio_idx = 1 + len(project.logos)
                if is_partial: args += ['-ss',str(start)]
                args += ['-i',str(final_audio.resolve())]
                if calc_duration is not None: args += ['-t',str(calc_duration)]
                audio_map = f'{audio_idx}:a:0'
            else:
                audio_map = '0:a:0?'

            args += ['-filter_complex_script',str(filter_script),'-map','[v]','-map',audio_map,
                     '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',
                     '-c:a','aac','-b:a','192k','-movflags','+faststart']
            if project.logos: args += ['-shortest']
            args += [str(temporary)]
            log.info(
                "[RENDER] mode=%s test_start=%s test_duration=%s source_video=%s output=%s "
                "subtitle_total=%d subtitle_selected=%d mask_events=%d filter_script_path=%s "
                "filter_script_chars=%d filter_script_bytes=%d argv_length=%d",
                "preview" if preview else ("test30" if duration is not None else "full"),
                start if is_partial else 0, calc_duration, project.source_video_path, destination,
                len(project.utterances), len(mask_events), len(mask_events), filter_script, len(chain),
                filter_script.stat().st_size, _argv_length(args),
            )
            if progress:
                if preview:
                    progress('FFmpeg đang render preview 10 giây…')
                elif duration is not None:
                    progress('FFmpeg đang render test 30 giây…')
                else:
                    progress('FFmpeg đang render toàn bộ video…')
            run_process(args,cancel,progress,cwd=folder)
            log.info("[RENDER] ffmpeg_started=true ffmpeg_exit_code=0")
            check_cancel(cancel)
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise RuntimeError('FFmpeg không tạo video export hợp lệ')
            os.replace(temporary, destination)
            if not export_job:
                state.update(status='completed',output=str(destination))
                atomic_json(folder/'render.json',state)
                filter_script.unlink(missing_ok=True)
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            state['status']='failed_or_cancelled'
            atomic_json(folder/'render.json',state)
            raise
        finally:
            if export_job:
                shutil.rmtree(folder, ignore_errors=True)
                export_root = root / '.tmp' / 'export'
                try:
                    export_root.rmdir(); export_root.parent.rmdir()
                except OSError:
                    pass
