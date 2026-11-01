"""Local source inspection and complete proposed settings, without starting an edit."""
from __future__ import annotations

import copy
from pathlib import Path
import re

from common import content_hash, probe_video
from project_contract import layout_for, settings_hash, source_identity_hash, validate_contract

VIDEO_EXTS = {'.mov', '.mp4', '.m4v', '.mkv', '.webm'}


def detect_language_sample(path: Path, duration: float) -> dict:
    """Use only an already-cached local model, with at most 30 seconds of decoded audio."""
    unknown = {'status': 'unknown', 'reason': 'No confident bounded local speech sample; keep source-language detection automatic'}
    try:
        import numpy as np
        from huggingface_hub import snapshot_download
        from transcribe import DEFAULT_MODEL, MLX_REPOS, default_engine, run_whisper
        from speech_regions import load_mono
        engine = default_engine()
        repo = MLX_REPOS[DEFAULT_MODEL] if engine == 'mlx' else f'Systran/faster-whisper-{DEFAULT_MODEL}'
        model = snapshot_download(repo, local_files_only=True)
        starts = [0] if duration <= 30 else [duration * 0.1, duration * 0.5, min(duration * 0.9, duration - 10)]
        length = min(duration, 30) if len(starts) == 1 else 10
        audio = np.concatenate([load_mono(path, start=start, end=start + length)[0] for start in starts])
        if len(audio) < 16000 or float(np.sqrt(np.mean(audio.astype(float) ** 2))) < 0.002:
            return unknown
        segments, language = run_whisper(audio, engine, model, None, None)
        words = [w for segment in segments for w in segment.get('words', [])]
        if not language or len(words) < 3:
            return unknown
        return {'status': 'detected', 'language': language, 'sampleSeconds': len(audio) / 16000,
                'scope': 'Representative sample only; does not prove a regional locale or monolingual recording'}
    except (ImportError, OSError, ValueError, RuntimeError):
        return unknown


def inspect_sources(paths: list[Path], detect_language: bool = True) -> dict:
    selected = []
    for value in paths:
        value = Path(value).expanduser().resolve()
        if not value.exists():
            raise ValueError(f'Source not found: {value}')
        if value.is_dir():
            selected.extend(p for p in sorted(value.iterdir()) if p.is_file() and not p.name.startswith('.') and p.suffix.lower() in VIDEO_EXTS)
        else:
            selected.append(value)
    selected = list(dict.fromkeys(p.resolve() for p in selected))
    if not selected:
        raise ValueError('No selected video sources were found')
    sources = []
    for index, path in enumerate(selected):
        source_hash = content_hash(path)
        facts = probe_video(path)
        if not facts.get('has_video'):
            raise ValueError(f'No video stream: {path.name}')
        language = detect_language_sample(path, facts['duration']) if detect_language and index < 3 and facts.get('has_audio') else {
            'status': 'unknown', 'reason': 'Speech is not inferred from prompt language'}
        if content_hash(path) != source_hash:
            raise ValueError(f'Source changed during inspection: {path.name}; inspect again')
        sources.append({'id': f'source-{index + 1:03d}', 'path': str(path), 'sha256': source_hash,
                        'size': path.stat().st_size, 'facts': facts, 'streams': [0],
                        'language': language})
    return {'schemaVersion': 1, 'sources': sources, 'sourceIdentityHash': source_identity_hash({'sources': sources}),
            'limitations': ['Bounded language sampling does not prove all spoken languages; no model is downloaded during inspection.'],
            'confirmationRequired': True}


def propose_settings(inspection: dict, requested: dict) -> dict:
    sources = inspection.get('sources', [])
    if not sources:
        raise ValueError('Select footage before proposing settings')
    if any(s['facts'].get('audio_tracks', 0) > 1 for s in sources):
        raise ValueError('Multi-track source editing is not implemented; select and prepare a single-track derivative explicitly before confirming this edit')
    facts = sources[0]['facts']
    dimensions = {(s['facts']['display_width'], s['facts']['display_height']) for s in sources}
    rates = {s['facts'].get('fps_str') for s in sources}
    colors = {tuple(s['facts'].get(k) for k in ('color_primaries', 'color_transfer', 'color_space', 'color_range')) for s in sources}
    requested_video = requested.get('video', {})
    if len(dimensions) > 1 and not {'width', 'height'} <= requested_video.keys():
        raise ValueError('Mixed source dimensions require an explicit output target')
    if len(rates) > 1 and 'frameRate' not in requested_video:
        raise ValueError('Mixed source frame rates require an explicit output target')
    if len(colors) > 1 and not requested.get('color'):
        raise ValueError('Mixed source color characteristics require an explicit common target')
    video = {'width': facts['display_width'], 'height': facts['display_height'],
             'frameRate': facts.get('fps_str'), 'timing': 'vfr' if facts.get('is_vfr') else 'cfr', 'fit': 'preserve'}
    video.update(requested_video)
    color = {'mode': 'preserve', 'target': 'source', 'grade': None, 'conversion': None, 'inputOverride': None}
    color.update(requested.get('color', {}))
    title = requested.get('title', 'My video')
    basename = re.sub(r'[^\w-]+', '-', title.lower(), flags=re.UNICODE).strip('-') or 'video'
    workflow = {'schemaVersion': 1, 'sourceIds': [s['id'] for s in sources],
                'language': {'conversation': requested.get('conversation', 'en-US'), 'transcription': 'auto',
                             'captions': 'source', 'translation': None},
                'editing': {'scope': requested.get('scope', 'full'), 'intervals': [], 'protectedIntervals': [], 'cleanup': 'standard'},
                'video': video, 'color': color,
                'encoding': {'container': 'mp4', 'videoCodec': 'auto', 'bitDepth': 'source', 'chroma': 'source'},
                'audio': {'mode': 'normalize' if facts.get('has_audio') and requested.get('scope', 'full') in ('full', 'audio', 'intervals') else 'preserve', 'targetLufs': -14.0,
                          'processingPeak': -1.5, 'truePeakLimit': -1.0, 'denoise': 'off', 'music': False, 'effects': False, 'tracks': 'source'},
                'visuals': {'scenes': 'off', 'allowedSceneIds': [], 'cameraMotion': False, 'transitions': 'off', 'backgroundTreatment': 'none'},
                'review': {'sample': 'agent'},
                'delivery': {'directory': '../../../exports', 'workspaceRoot': str(Path.home() / 'Downloads/editonny'),
                             'executionContext': 'local', 'basename': basename, 'subtitleFormats': [], 'variants': []}}
    for section in ('language', 'editing', 'encoding', 'audio', 'visuals', 'review', 'delivery'):
        workflow[section].update(copy.deepcopy(requested.get(section, {})))
    config = validate_contract({'title': title, 'edit': '../edit', 'format': layout_for(video['width'], video['height']),
                               'style': requested.get('style', 'paper'), 'workflow': workflow,
                               'captions': {'enabled': requested.get('captions', True), 'style': 'standard', 'maxWords': 3, 'inFrame': False},
                               'blocks': [{'scene': 'fullscreen', 'start': 0, 'zoom': 1.0}], 'animations': [], 'sounds': []})
    summary = {
        'intent': f"{workflow['editing']['scope']} edit; preserve meaning and requested scope",
        'sources': [s['path'] for s in sources],
        'image': 'Original image, no color treatment' if color['mode'] == 'preserve' else color,
        'format': {'video': video, 'encoding': workflow['encoding']},
        'language': {**workflow['language'], 'observedSpeech': [s.get('language', {'status': 'unknown'}) for s in sources]},
        'captions': config['captions'],
        'cleanup': workflow['editing'],
        'audio': workflow['audio'],
        'graphics': workflow['visuals'],
        'reviewAndDelivery': {'review': workflow['review'], 'delivery': workflow['delivery'],
                              'location': config['workflow']['delivery']['workspaceRoot']},
    }
    return {'config': config, 'summary': summary, 'settingsHash': settings_hash(config),
            'sourceIdentityHash': source_identity_hash(inspection), 'confirmationRequired': True,
            # A snapshot: callers attach the proposal to the same inspection report they print.
            'provenance': {'requested': requested, 'observed': copy.deepcopy(inspection), 'defaults': 'proposed, not approved'}}
