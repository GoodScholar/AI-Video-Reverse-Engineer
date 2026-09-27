"""JSON-lines bridge; model/generation are supplied by MLX-Audio, not reimplemented."""
import contextlib
import json
import os
import sys
from pathlib import Path

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
model = None
for line in sys.stdin:
    try:
        request = json.loads(line)
        with contextlib.redirect_stdout(sys.stderr):
            import mlx.core as mx
            import numpy as np
            import soundfile as sf
            from mlx_audio.tts.utils import load_model
            if model is None:
                model = load_model(Path(sys.argv[1]))
            assert request['voice'] in model.supported_speakers
            directory = Path(request['directory'])
            for index, text in enumerate(request['texts']):
                mx.random.seed(42)
                parts = []
                for result in model.generate(text=text, voice=request['voice'], lang_code='Chinese', temperature=0.9,
                                             max_tokens=1600, verbose=False):
                    mx.eval(result.audio)
                    parts.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
                audio = np.concatenate(parts)
                if not np.isfinite(audio).all() or np.max(np.abs(audio)) < .001 or np.sqrt(np.mean(audio*audio)) < .0001:
                    raise ValueError('invalid audio')
                sf.write(directory / f'{index}.wav', audio, result.sample_rate, subtype='PCM_16')
        response = {'status': 'completed'}
    except Exception:
        response = {'status': 'failed'}
    print(json.dumps(response), flush=True)
