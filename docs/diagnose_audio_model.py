"""Run reproducible cloud-only transcription checks; never sends reference notes."""
import argparse
import array
import ast
import asyncio
import base64
import hashlib
import json
import math
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from dotenv import load_dotenv
from app.audio_understanding import QwenOmniClient, _validate_notes
from app.ir import melody_to_midi

OUT = ROOT / 'data/demo/model-audit'

def control():
    path = OUT / 'control.wav'
    reference = [(57, .5, .6), (64, 1.3, .8), (60, 2.4, .5), (67, 3.2, 1.0), (59, 4.6, .7)]
    rate = 16000
    samples = array.array('h', [0]) * (rate * 6)
    for pitch, start, duration in reference:
        frequency = 440 * 2 ** ((pitch - 69) / 12)
        for i in range(int(duration * rate)):
            t = i / rate
            envelope = min(1, t / .02, (duration - t) / .02)
            samples[int(start * rate) + i] = int(12000 * envelope * math.sin(2 * math.pi * frequency * t))
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        f.writeframes(samples.tobytes())
    (OUT / 'control_reference.json').write_text(json.dumps(reference), encoding='utf-8')
    return path

async def run(path, model, prompt):
    adapter = QwenOmniClient()
    data = path.read_bytes()
    with wave.open(str(path), 'rb') as f:
        spec = dict(channels=f.getnchannels(), rate=f.getframerate(), duration=f.getnframes()/f.getframerate())
    payload = dict(model=model, stream=True, stream_options={'include_usage': True}, max_tokens=2048,
                   modalities=['text'], messages=[{'role':'user', 'content':[
                       {'type':'input_audio','input_audio':{'data':'data:audio/wav;base64,'+base64.b64encode(data).decode(), 'format':'wav'}},
                       {'type':'text','text':prompt}]}])
    record = dict(requested_model=model, audio=spec, sha256=hashlib.sha256(data).hexdigest(), prompt=prompt)
    parts = []
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream('POST', adapter.endpoint, headers={'Authorization':'Bearer '+adapter.api_key}, json=payload) as response:
            record['http_status'] = response.status_code
            if response.is_error:
                record['error'] = (await response.aread()).decode()[:1000]
            else:
                async for line in response.aiter_lines():
                    if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                        continue
                    event = json.loads(line[5:])
                    for key in ('model', 'id', 'usage'):
                        if event.get(key) is not None:
                            record[key] = event[key]
                    for choice in event.get('choices', []):
                        if choice.get('finish_reason'):
                            record['finish_reason'] = choice['finish_reason']
                        value = choice.get('delta', {}).get('content')
                        if isinstance(value, str):
                            parts.append(value)
    record['raw_text'] = ''.join(parts)
    try:
        raw = record['raw_text']
        analysis = _validate_notes(json.loads(raw[raw.index('{'):raw.rindex('}')+1]))
        midi, ir = melody_to_midi(analysis)
        record['analysis'] = analysis
        record['midi_roundtrip_pitches'] = [n['pitch'] for n in ir['note_events']]
        (OUT / (model + '-' + path.stem + '.mid')).write_bytes(midi)
    except Exception as exc:
        record['parse_error'] = str(exc)
    target = OUT / (model + '-' + path.stem + '.json')
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k not in ('prompt','sha256')}, ensure_ascii=True))

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='qwen3.5-omni-plus')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    OUT.mkdir(parents=True, exist_ok=True)
    tree = ast.parse((ROOT / 'app/audio_understanding.py').read_text(encoding='utf-8'))
    prompt = next(ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='prompt' for t in n.targets))
    await asyncio.gather(run(control(), args.model, prompt), run(ROOT/'data/demo/model-compare/audio_16k.wav', args.model, prompt))

if __name__ == '__main__':
    asyncio.run(main())
