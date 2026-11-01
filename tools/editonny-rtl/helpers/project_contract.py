"""Canonical decisions and confirmation boundaries for managed editing projects."""
from __future__ import annotations

import copy
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return value


def frame_rate(value: object) -> Fraction:
    try:
        rate = Fraction(str(value))
    except (ValueError, TypeError, ZeroDivisionError) as error:
        raise ValueError('frameRate must be an exact positive rational number') from error
    if not 0 < rate <= 240:
        raise ValueError('frameRate must be positive and no greater than 240')
    return rate


def layout_for(width: int, height: int) -> str:
    return {(9, 16): '9:16', (16, 9): '16:9', (4, 5): '4:5', (1, 1): '1:1'}.get(
        (width // math.gcd(width, height), height // math.gcd(width, height)), 'source')


def _choice(section: dict, key: str, values: tuple) -> None:
    if section.get(key) not in values:
        raise ValueError(f'{key} must be one of {", ".join(str(v) for v in values)}')


def validate_contract(config: dict) -> dict:
    config = copy.deepcopy(config)
    workflow = config.get('workflow')
    if not isinstance(workflow, dict) or workflow.get('schemaVersion') != 1:
        raise ValueError('A confirmed workflow schemaVersion 1 is required; inspect the sources first')
    if config.get('edit') != '../edit' or config.get('video', 'cut.mp4') != 'cut.mp4':
        raise ValueError('Managed edit and cut paths are fixed to ../edit and cut.mp4 inside this revision')
    for section in ('language', 'editing', 'video', 'color', 'encoding', 'audio', 'visuals', 'review', 'delivery'):
        if not isinstance(workflow.get(section), dict):
            raise ValueError(f'workflow.{section} must be an object')
    ids = workflow.get('sourceIds')
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('sourceIds must select unique observed sources')
    video = workflow['video']
    for key in ('width', 'height'):
        n = video.get(key)
        if isinstance(n, bool) or not isinstance(n, int) or n < 16 or n > 16384 or n % 2:
            raise ValueError(f'{key} must be a positive even integer between 16 and 16384; do not round silently')
    _choice(video, 'timing', ('cfr', 'vfr'))
    _choice(video, 'fit', ('preserve', 'contain', 'crop'))
    if video['timing'] == 'cfr' or video.get('frameRate') is not None:
        video['frameRate'] = str(frame_rate(video.get('frameRate')))
    expected = layout_for(video['width'], video['height'])
    if config.get('format', expected) != expected:
        raise ValueError('format conflicts with workflow.video dimensions')
    config['format'] = expected
    color = workflow['color']
    _choice(color, 'mode', ('preserve', 'convert'))
    _choice(color, 'target', ('source', 'sdr-bt709', 'hdr-pq-bt2020', 'hdr-hlg-bt2020'))
    if color['mode'] == 'preserve' and (color.get('grade') or color.get('conversion') or color['target'] != 'source'):
        raise ValueError('Preserve image forbids unrequested grading or color conversion')
    if color['mode'] == 'convert' and not (color.get('grade') or color.get('conversion')):
        raise ValueError('Color conversion must identify the requested treatment or technical transform')
    interpretation = color.get('inputOverride')
    if interpretation is not None:
        fields = {'color_primaries': ('bt709', 'smpte170m', 'bt470bg', 'bt2020'),
                  'color_transfer': ('bt709', 'smpte170m', 'iec61966-2-1', 'smpte2084', 'arib-std-b67'),
                  'color_space': ('bt709', 'smpte170m', 'bt470bg', 'bt2020nc'), 'color_range': ('pc', 'tv')}
        if not isinstance(interpretation, dict) or not interpretation or any(k not in fields or v not in fields[k] for k, v in interpretation.items()):
            raise ValueError('inputOverride must explicitly identify supported color metadata; unknown values cannot be guessed')
    _choice(workflow['editing'], 'scope', ('full', 'captions', 'intervals', 'audio', 'visual'))
    if workflow['editing']['scope'] == 'intervals' and not workflow['editing'].get('intervals'):
        raise ValueError('An interval revision requires explicit source intervals')
    for key in ('intervals', 'protectedIntervals'):
        intervals = workflow['editing'].get(key, [])
        if not isinstance(intervals, list):
            raise ValueError(f'{key} must be a list')
        for interval in intervals:
            if not isinstance(interval, dict) or interval.get('sourceId') not in ids:
                raise ValueError(f'{key} must reference a selected sourceId')
            start, end = interval.get('start'), interval.get('end')
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (start, end)) or not 0 <= start < end:
                raise ValueError(f'{key} requires finite source times: 0 <= start < end')
    language = workflow['language']
    for key in ('conversation', 'transcription', 'captions'):
        if not isinstance(language.get(key), str) or not language[key].strip():
            raise ValueError(f'language.{key} is required')
    if language.get('translation') not in (None, False):
        raise ValueError('Automatic caption translation is not implemented; preserve source language or provide a separately verified supported translation workflow')
    audio = workflow['audio']
    if audio.get('tracks') != 'source':
        raise ValueError('Explicit audio track selection is not implemented; provide a separately prepared and confirmed single-track source')
    _choice(audio, 'mode', ('preserve', 'normalize'))
    _choice(audio, 'denoise', ('off', 'light', 'strong'))
    for key in ('music', 'effects'):
        if not isinstance(audio.get(key), bool):
            raise ValueError(f'audio.{key} must be an explicit boolean')
    for key, low, high in (('targetLufs', -40, -5), ('processingPeak', -12, 0), ('truePeakLimit', -12, 0)):
        value = audio.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'audio.{key} is invalid')
    if audio['processingPeak'] > audio['truePeakLimit']:
        raise ValueError('Processing peak must leave headroom below the encoded peak limit')
    _choice(workflow['review'], 'sample', ('agent', 'user'))
    visuals = workflow['visuals']
    for key in ('scenes', 'transitions'):
        _choice(visuals, key, ('off', 'requested', 'agent'))
    if not isinstance(visuals.get('cameraMotion'), bool):
        raise ValueError('visuals.cameraMotion must be explicit')
    encoding = workflow['encoding']
    _choice(encoding, 'container', ('mp4',))
    _choice(encoding, 'videoCodec', ('auto', 'source', 'h264', 'hevc'))
    _choice(encoding, 'bitDepth', ('source', 8, 10, 12))
    _choice(encoding, 'chroma', ('source', '420', '422', '444'))
    captions = config.get('captions', {})
    if not isinstance(captions, dict) or not isinstance(captions.get('enabled'), bool):
        raise ValueError('Caption visibility must be explicit')
    if not isinstance(captions.get('maxWords'), int) or isinstance(captions['maxWords'], bool) or not 1 <= captions['maxWords'] <= 10:
        raise ValueError('Caption maxWords must be between 1 and 10')
    delivery = workflow['delivery']
    root = delivery.get('workspaceRoot')
    if not isinstance(root, str) or not root.strip() or not Path(root).expanduser().is_absolute():
        raise ValueError('delivery.workspaceRoot must identify the confirmed absolute workspace destination')
    delivery['workspaceRoot'] = str(Path(root).expanduser().resolve())
    _choice(delivery, 'executionContext', ('local', 'remote'))
    if delivery.get('directory') != '../../../exports':
        raise ValueError('Managed delivery directory must be ../../../exports, the shared exports folder of the job')
    basename = delivery.get('basename')
    if not isinstance(basename, str) or not basename or any(c in basename for c in ('/', '\\', '\x00')) or basename.startswith('.'):
        raise ValueError('delivery.basename must be a safe visible filename')
    if not isinstance(delivery.get('subtitleFormats', []), list) or any(f not in ('srt', 'vtt') for f in delivery.get('subtitleFormats', [])):
        raise ValueError('subtitleFormats supports srt and vtt')
    if not isinstance(delivery.get('variants', []), list):
        raise ValueError('delivery.variants must be a list')
    seen = set()
    for variant in delivery.get('variants', []):
        if not isinstance(variant, dict) or set(variant) - {'name', 'video', 'color', 'encoding'}:
            raise ValueError('Variants may override only named video/color/encoding settings')
        name = variant.get('name', '')
        if not isinstance(name, str) or not name or name in seen or not all(c.isalnum() or c in '-_' for c in name):
            raise ValueError('Variant names must be unique safe identifiers')
        seen.add(name)
        resolved = copy.deepcopy(config)
        resolved['workflow']['delivery']['variants'] = []
        for section in ('video', 'color', 'encoding'):
            if section in variant:
                if not isinstance(variant[section], dict):
                    raise ValueError(f'Variant {section} must be an object')
                resolved['workflow'][section].update(variant[section])
        v = resolved['workflow']['video']
        resolved['format'] = layout_for(v['width'], v['height'])
        validate_contract(resolved)
    digest(config)
    return config


def load_contract(project: Path) -> dict:
    return validate_contract(read_json(Path(project) / 'project.json'))


def validate_caption_language(config: dict, transcript: dict) -> None:
    language = config['workflow']['language']
    target = language['captions']
    if target == 'source':
        return
    speech = transcript.get('language') or language['transcription']
    if speech == 'auto' or not speech or speech.split('-')[0].lower() != target.split('-')[0].lower():
        raise ValueError('Caption target differs from measured speech; original words cannot be labeled as translated captions')


def settings_hash(contract: dict) -> str:
    config = validate_contract(contract)
    workflow = copy.deepcopy(config['workflow'])
    workflow['review'].pop('sampleIntervals', None)
    captions = config.get('captions', {})
    return digest({'workflow': workflow, 'style': config.get('style'),
                   'captions': {k: captions.get(k) for k in ('enabled', 'style', 'maxWords', 'inFrame')}})


def source_identity_hash(manifest: dict) -> str:
    return digest(sorted(({'id': s['id'], 'sha256': s['sha256'], 'streams': s.get('streams')}
                          for s in manifest.get('sources', [])), key=lambda s: s['id']))


def validate_confirmation(config: dict, identity: str, confirmation: dict) -> dict:
    if not isinstance(confirmation, dict) or confirmation.get('approved') is not True:
        raise ValueError('Wait for explicit confirmation of the displayed settings summary')
    if confirmation.get('settingsHash') != settings_hash(config) or confirmation.get('sourceIdentityHash') != identity:
        raise ValueError('Confirmation is stale: sources or settings changed; present the new summary')
    if not confirmation.get('summary') or not confirmation.get('response'):
        raise ValueError('Confirmation requires the actual summary and user response')
    if not confirmation.get('summaryId') or confirmation.get('responseTo') != confirmation['summaryId']:
        raise ValueError('Response must identify the displayed summary')
    if not confirmation.get('summaryMessageId') or not confirmation.get('responseMessageId') or confirmation['summaryMessageId'] == confirmation['responseMessageId']:
        raise ValueError('Initial editing prompt cannot confirm its own settings summary')
    try:
        presented = datetime.fromisoformat(confirmation['presentedAt'])
        responded = datetime.fromisoformat(confirmation['respondedAt'])
        if not presented.tzinfo or not responded.tzinfo or responded <= presented:
            raise ValueError('Response must follow the settings summary')
    except (KeyError, TypeError) as error:
        raise ValueError('Confirmation requires summary and response timestamps') from error
    return copy.deepcopy(confirmation)


def require_confirmation(project: Path, source_identity: str) -> dict:
    config = load_contract(project)
    edit = (Path(project) / config['edit']).resolve()
    state = read_json(edit / 'state.json') if (edit / 'state.json').is_file() else {}
    if state.get('acceptedVariant'):
        variant = state['acceptedVariant']
        parent = Path(variant['parentProject'])
        expected = resolve_variant(load_contract(parent), variant['name'])
        if settings_hash(expected) != settings_hash(config):
            raise ValueError('Variant revision no longer matches the variant accepted in its parent summary')
        return require_confirmation(parent, source_identity)
    return validate_confirmation(config, source_identity, state.get('settingsConfirmation'))


def resolve_variant(config: dict, name: str) -> dict:
    result = validate_contract(config)
    variant = next((v for v in result['workflow']['delivery']['variants'] if v['name'] == name), None)
    if variant is None:
        raise ValueError('Output variant was not included in the confirmed summary')
    for section in ('video', 'color', 'encoding'):
        result['workflow'][section].update(variant.get(section, {}))
    result['workflow']['delivery']['variants'] = []
    video = result['workflow']['video']
    result['format'] = layout_for(video['width'], video['height'])
    return validate_contract(result)


def artifact_dependencies(contract: dict, sources: dict, assets: dict, tools: dict) -> dict:
    return {'settingsHash': settings_hash(contract), 'configurationHash': digest(contract),
            'sourcesHash': digest(sources), 'assetsHash': digest(assets), 'toolsHash': digest(tools)}


def managed_config(edit: Path, operation: str | None = None) -> dict | None:
    """Apply gates to a managed revision while keeping standalone engineering helpers usable."""
    edit = Path(edit).resolve()
    candidate = edit.parent / 'project'
    if not (candidate / 'project.json').is_file():
        return None
    raw = read_json(candidate / 'project.json')
    if not raw.get('workflow'):
        if (edit / 'source_manifest.json').exists():
            raise ValueError('Managed project workflow is missing; restore its confirmed settings')
        return None
    config = validate_contract(raw)
    manifest = read_json(edit / 'source_manifest.json')
    from workspace import selected_sources, verify_sources
    manifest = selected_sources(manifest, config)
    verify_sources(manifest)
    require_confirmation(candidate, source_identity_hash(manifest))
    visuals = config['workflow']['visuals']
    if visuals['scenes'] == 'off' and (config.get('scenes') or config.get('animations')):
        raise ValueError('Additional scenes/animations were not confirmed')
    if visuals['scenes'] == 'off' and (config.get('endCard', {}).get('enabled') or any(b.get('scene', 'fullscreen') not in ('fullscreen', 'contain') for b in config.get('blocks', []))):
        raise ValueError('Scene layouts/end cards were not confirmed')
    if not visuals['cameraMotion'] and any(b.get('zoom', 1) != 1 or b.get('zoomEnd', 1) != 1 for b in config.get('blocks', [])):
        raise ValueError('Camera zoom or movement was not confirmed')
    if not config['workflow']['audio'].get('effects') and config.get('sounds'):
        raise ValueError('Sound effects were not confirmed')
    if operation == 'cut' and config['workflow']['editing']['scope'] not in ('full', 'intervals'):
        raise ValueError('This confirmed revision does not authorize recutting; reuse its approved cut')
    return config


def validate_edl_scope(config: dict, manifest: dict, edl: dict, parent_edl: dict | None = None) -> None:
    from workspace import selected_sources
    sources = {s['id']: str(Path(s['path']).resolve()) for s in selected_sources(manifest, config)['sources']}
    paths = {key: str(Path(path).resolve()) for key, path in edl['sources'].items()}
    if any(path not in sources.values() for path in paths.values()):
        raise ValueError('EDL includes a source outside the confirmed selection')
    editing = config['workflow']['editing']
    if editing['scope'] not in ('full', 'intervals'):
        raise ValueError('This editing scope does not authorize recutting')
    if editing['scope'] == 'intervals':
        if parent_edl is None:
            raise ValueError('Interval scope needs its parent EDL to preserve all unrelated content')
        def outside_ranges(document):
            result = []
            source_paths = {k: str(Path(p).resolve()) for k, p in document['sources'].items()}
            for interval in document['ranges']:
                path = source_paths[interval['source']]
                spans = [(interval['start'], interval['end'])]
                for window in editing['intervals']:
                    if path != sources[window['sourceId']]:
                        continue
                    spans = [(x, y) for a, b in spans for x, y in ((a, min(b, window['start'])), (max(a, window['end']), b)) if y > x]
                treatment = digest({'crop': interval.get('crop') or None, 'gain_db': interval.get('gain_db') or 0,
                                    'picture_source': source_paths.get(interval.get('picture_source'))})
                for a, b in spans:
                    item = (path, round(a, 6), round(b, 6), treatment)
                    if result and result[-1][0] == path and result[-1][2] == item[1] and result[-1][3] == treatment:
                        result[-1] = (path, result[-1][1], item[2], treatment)
                    else:
                        result.append(item)
            return result
        if outside_ranges(edl) != outside_ranges(parent_edl):
            raise ValueError('EDL changes content outside the confirmed interval scope; preserve its parent spans and order')
    for protected in editing.get('protectedIntervals', []):
        spans = sorted((r['start'], r['end']) for r in edl['ranges'] if paths[r['source']] == sources[protected['sourceId']])
        cursor = protected['start']
        for start, end in spans:
            if start <= cursor:
                cursor = max(cursor, end)
        if cursor < protected['end']:
            raise ValueError('EDL removes content from a protected source interval')
