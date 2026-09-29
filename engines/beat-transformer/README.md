# MusicLab Beat-Transformer runtime

This directory is the complete model-side runtime used by MusicLab's rhythm
analysis. It contains the upstream MIT-licensed architecture files and the
validated `fold_4_trf_param.pt` checkpoint. Inference is launched with the
current MusicLab virtual environment and `beat_worker.py`; no WSL installation,
other checkout, service, or application is required.

Expected SHA-256 hashes:

- `fold_4_trf_param.pt`: `b76033014dd07d12307743b92337b7dffadf1f20a6ccd5a1edb03276e99a4512`
- `DilatedTransformer.py`: `54f8e13f93ff02f5070ff11095929264157275ab9975f06b233a373539873ec8`
- `DilatedTransformerLayer.py`: `87bdb9e11da791378c2aaa8ade815e6ea9eec2155dd700402b6561dbb8e2330b`
