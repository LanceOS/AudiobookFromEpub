#!/usr/bin/env python3
"""
Scaffold: convert EPUB -> audio using Kokoro TTS if available.

Behavior:
- If the `kokoro` package is importable, uses `KPipeline` to synthesize `--text` or first chapter of `--epub`.
- Otherwise inspects the local checkpoint at `--checkpoint` and prints metadata to help integration.

This file is a scaffold; Kokoro's PyPI package (kokoro>=0.9.2) currently requires Python <3.13.
If you want to run Kokoro end-to-end, create a Python 3.11/3.12 venv and install `kokoro` there.
"""

from pathlib import Path
import argparse
import logging
import sys

import torch
import soundfile as sf
from ebooklib import epub
import ebooklib
from bs4 import BeautifulSoup

try:
    from kokoro import KPipeline
    _HAS_KOKORO = True
except Exception as e:
    KPipeline = None
    _HAS_KOKORO = False
    _KOKORO_IMPORT_ERR = e


def extract_text_from_epub(epub_path, max_chars=4000):
    book = epub.read_epub(str(epub_path))
    texts = []
    try:
        items = book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
    except Exception:
        items = list(book.get_items())
    for item in items:
        try:
            content = item.get_content()
        except Exception:
            content = getattr(item, 'content', None)
        if not content:
            continue
        soup = BeautifulSoup(content, "lxml")
        t = soup.get_text(separator=' ', strip=True)
        if t:
            texts.append(t)
        if sum(len(x) for x in texts) >= max_chars:
            break
    return "\n\n".join(texts)[:max_chars]


def detect_device(preferred='auto'):
    pref = (preferred or 'auto').lower()
    if pref == 'auto':
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    if pref == 'cuda' and not torch.cuda.is_available():
        logging.warning('CUDA requested but not available; falling back to cpu')
        return 'cpu'
    return pref


def inspect_checkpoint(path, map_location='cpu'):
    path = Path(path)
    if not path.exists():
        print(f"Checkpoint not found at {path}")
        return
    print(f"Loading checkpoint (map_location={map_location}) from {path} ...")
    try:
        ckpt = torch.load(str(path), map_location=map_location)
    except Exception as e:
        print('Failed to load checkpoint:', e)
        return
    print('Loaded checkpoint type:', type(ckpt))
    if isinstance(ckpt, dict):
        keys = list(ckpt.keys())
        print('Top-level keys:', keys[:50])
        for k in keys[:50]:
            v = ckpt[k]
            t = type(v).__name__
            if hasattr(v, 'shape'):
                print(f"  {k}: {t} shape={getattr(v,'shape',None)}")
            else:
                print(f"  {k}: {t}")
    else:
        print('Checkpoint content (non-dict):', str(ckpt)[:200])


def synthesize_with_kokoro(text, voice='af_heart', out='output.wav', lang_code=None):
    if not _HAS_KOKORO:
        raise RuntimeError('kokoro package not available: ' + str(_KOKORO_IMPORT_ERR))
    if lang_code is None:
        if '_' in voice:
            lang_code = voice.split('_')[0][0]
        else:
            lang_code = 'a'
    # construct pipeline (KPipeline may accept lang_code kw)
    try:
        pipeline = KPipeline(lang_code=lang_code)
    except TypeError:
        pipeline = KPipeline()
    print('Generating audio with Kokoro pipeline...')
    generator = pipeline(text, voice=voice)
    for i, (gs, ps, audio) in enumerate(generator):
        outp = Path(out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(outp), audio, 24000)
        print(f'Wrote {outp}')
        break


def main():
    parser = argparse.ArgumentParser(description='EPUB -> Audio scaffolder using Kokoro (scaffold)')
    parser.add_argument('--epub', type=Path, help='Path to EPUB file')
    parser.add_argument('--text', type=str, help='Text to synthesize (if omitted, extracts first chapter of EPUB)')
    parser.add_argument('--voice', default='af_heart', help='Voice name from VOICES.md (default af_heart)')
    parser.add_argument('--device', default='auto', choices=['auto', 'cpu', 'cuda'], help='Device to run on')
    parser.add_argument('--out', default='output.wav', help='Output WAV file')
    parser.add_argument('--checkpoint', default='Kokoro-82M/kokoro-v1_0.pth', help='Path to local checkpoint (for inspection)')
    parser.add_argument('--max-chars', type=int, default=2000)
    args = parser.parse_args()

    device = detect_device(args.device)
    print(f'Device: {device}')

    text = args.text
    if args.epub and not text:
        print(f'Extracting text from {args.epub} ...')
        text = extract_text_from_epub(args.epub, max_chars=args.max_chars)
    if not text:
        text = 'Hello from Kokoro scaffold. Provide --text or --epub to synthesize real content.'
    print('Text preview:', text[:200].replace('\n', ' '))

    if _HAS_KOKORO:
        try:
            synthesize_with_kokoro(text, voice=args.voice, out=args.out)
            return
        except Exception as e:
            print('kokoro synthesis failed:', e)
            print('Falling back to checkpoint inspection.')

    inspect_checkpoint(args.checkpoint, map_location=device)


if __name__ == '__main__':
    main()
