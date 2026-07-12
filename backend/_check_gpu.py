import torch
try:
    print("CUDA:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("GPU:", torch.cuda.get_device_name(0))
        print("VRAM:", round(torch.cuda.get_device_properties(0).total_mem/1024**3, 1), "GB")
    else:
        print("GPU: N/A (CPU only)")
except Exception as e:
    print("Error:", e)

import sentence_transformers
print("sentence-transformers:", sentence_transformers.__version__)
