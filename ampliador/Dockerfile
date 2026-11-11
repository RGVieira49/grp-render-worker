# Ampliador fotográfico do Estúdio GRP Arquitetura (RunPod Serverless, GPU NVIDIA).
# Só código e bibliotecas: os pesos dos modelos (~10 GB, licenças comerciais) vão para o volume de rede
# no primeiro job, em revisões fixas (ver ampliador.py). Nenhum dado da GRP entra na imagem.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install diffusers==0.31.0 transformers==4.46.3 accelerate==1.1.1 safetensors==0.4.5 \
        huggingface_hub==0.26.2 pillow==11.0.0 runpod==1.12.0

COPY ampliador.py recombinar.py /app/
CMD ["python", "-u", "/app/ampliador.py"]
