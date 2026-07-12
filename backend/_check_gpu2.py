import sys
import torch

output = []
try:
    output.append("CUDA: " + str(torch.cuda.is_available()))
    if torch.cuda.is_available():
        output.append("GPU: " + str(torch.cuda.get_device_name(0)))
        output.append("VRAM: " + str(round(torch.cuda.get_device_properties(0).total_mem/1024**3, 1)) + " GB")
    else:
        output.append("GPU: N/A (CPU only)")
except Exception as e:
    output.append("torch error: " + str(e))

try:
    import sentence_transformers
    output.append("sentence-transformers: " + sentence_transformers.__version__)
except Exception as e:
    output.append("sentence_transformers: " + str(e))

try:
    from transformers import __version__ as tv
    output.append("transformers: " + tv)
except Exception as e:
    output.append("transformers: " + str(e))

try:
    import datasets
    output.append("datasets: " + datasets.__version__)
except Exception as e:
    output.append("datasets: " + str(e))

with open(r"D:\python\PythonProject\RAG+LLMProject\backend\_gpu_info.txt", "w") as f:
    f.write("\n".join(output))
