# Trabalhador de render do Estúdio GRP Arquitetura (RunPod Serverless, GPU NVIDIA).
# Só Blender + scripts; nenhum dado da GRP entra na imagem: o modelo chega por URL a cada job.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils python3 python3-venv \
        libx11-6 libxext6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 \
        libsm6 libice6 libgl1 libegl1 libglib2.0-0 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

# Blender oficial, conferido pelo sha256 publicado em download.blender.org
ARG BLENDER_VERSION=5.3.0
ARG BLENDER_SHA256=dacb8264197a3f7fda6001285a5a506fc97ee943881261c2c3bd63c882f22e05
RUN curl -fsSL -o /tmp/blender.tar.xz \
        https://download.blender.org/release/Blender5.3/blender-${BLENDER_VERSION}-linux-x64.tar.xz \
    && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz

# Céu fotográfico CC0 (Poly Haven, kloofendal_48d_partly_cloudy_puresky, 4K)
ARG CEU_SHA256=3061c00a16ecae748e84fd6c44c04804f539c118bb66325db858bad87b11bf88
RUN mkdir -p /opt/ceu \
    && curl -fsSL -o /opt/ceu/ceu_4k.hdr \
        https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/4k/kloofendal_48d_partly_cloudy_puresky_4k.hdr \
    && echo "${CEU_SHA256}  /opt/ceu/ceu_4k.hdr" | sha256sum -c -

# Jardim com árvores (Poly Haven, pretoria_gardens, 4K, CC0): o que se vê pelas janelas do decorado
ARG JARDIM_SHA256=e0ab5ccc71616365835f4fc98b391d242dbe2577107265f848100e610b08f13c
RUN curl -fsSL -o /opt/ceu/jardim_4k.hdr \
        https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/4k/pretoria_gardens_4k.hdr \
    && echo "${JARDIM_SHA256}  /opt/ceu/jardim_4k.hdr" | sha256sum -c -

RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir runpod==1.12.0

COPY handler.py /app/handler.py
COPY scripts /app/scripts

CMD ["/opt/venv/bin/python", "-u", "/app/handler.py"]
