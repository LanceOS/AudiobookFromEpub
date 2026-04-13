import importlib
mods = ["torch","torchaudio","ebooklib","bs4","librosa","sentencepiece","numpy","soundfile","lxml","tqdm","scipy"]
for m in mods:
    try:
        importlib.import_module(m)
        status = "OK"
    except Exception as e:
        status = f"ERR: {e.__class__.__name__}: {e}"
    print(f"{m}: {status}")
