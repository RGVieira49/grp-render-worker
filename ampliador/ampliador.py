"""Ampliador fotográfico do Estúdio GRP Arquitetura (RunPod Serverless, GPU NVIDIA).

Mesmo princípio do Magnific: parte do render físico, pixel a pixel, e acrescenta só o micro-detalhe de foto
(veio, trama, poros, pequenas imperfeições) em baixa intensidade. O ControlNet Tile prende cada ladrilho ao
contorno do render, então nada muda de lugar; no fim, a luz e a cor "grandes" voltam a ser as do render
(frequências baixas), para a IA não mexer na iluminação.

Entrada do job:
    imagem_url    link https do render (assinado, expira)
    upload_url    link https (assinado, uso único) para devolver o JPEG por PUT
    escala        1.0 a 2.0 (padrão 1.5: 4K → 5760×3240; 2.0 → 8K)
    forca         intensidade do refino, 0.15 a 0.65 (padrão 0.5, calibrado no Kala em 11/11)
    detalhe       força do ControlNet Tile, 0.5 a 1.0 (padrão 0.6)
    guia          CFG, 2 a 10 (padrão 7)
    fidelidade    0 a 1: quanto dos meios-tons volta do render (padrão 0.3; ver recombinar.py)
    contraste     ganho máximo do contraste local, 1.0 a 2.0 (padrão 1.5: tira o "véu" da IA)
    passos, semente, prompt_extra   opcionais

Modelos (licenças comerciais): RealVisXL V5.0 (openrail++), xinsir/controlnet-tile-sdxl-1.0 (Apache-2.0),
madebyollin/sdxl-vae-fp16-fix (MIT). Baixados uma vez para o volume de rede (revisões fixas).
"""
import io, json, math, os, time, urllib.request

VOLUME = '/runpod-volume' if os.path.isdir('/runpod-volume') else '/tmp'
os.environ.setdefault('HF_HOME', os.path.join(VOLUME, 'hf'))
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')

import numpy as np
import torch
from PIL import Image
from diffusers import (AutoencoderKL, ControlNetModel, DPMSolverMultistepScheduler,
                       StableDiffusionXLControlNetImg2ImgPipeline)
import runpod
from huggingface_hub import snapshot_download
from recombinar import recombinar

BASE = ('SG161222/RealVisXL_V5.0', 'ac93e0dda1f6d448cae19bbfab8c5e720a5e48bc')
TILE = ('xinsir/controlnet-tile-sdxl-1.0', '1ae8d9529efe58f7362a987363ff86a7904dc84f')
VAE = ('madebyollin/sdxl-vae-fp16-fix', '207b116dae70ace3637169f1ddd2434b91b3a8cd')

PROMPT = ('professional architectural interior photograph, high-end real estate photography, shot on full-frame camera, '
          'natural soft daylight, physically accurate materials, fine natural wood veneer grain, visible fabric weave, '
          'linen and boucle upholstery texture, matte paint with subtle texture, crisp sharp details, true-to-life colors, '
          'photorealistic')
NEGATIVE = ('cgi, 3d render, rendering, cartoon, illustration, painting, drawing, plastic, waxy, smooth flat surfaces, '
            'blurry, soft focus, noise, jpeg artifacts, oversaturated, text, letters, logo, brand, watermark, '
            'people, person, distorted, deformed')

LADO = 1024   # ladrilho nativo do SDXL
SOBRA = 256   # sobreposição entre ladrilhos (emenda suave)

t_carga = time.time()
dtype = torch.float16
vae = AutoencoderKL.from_pretrained(VAE[0], revision=VAE[1], torch_dtype=dtype)
controlnet = ControlNetModel.from_pretrained(TILE[0], revision=TILE[1], torch_dtype=dtype)
# só os pesos fp16 (o repositório também traz a rede principal em fp32, ~10 GB que não usamos)
pasta_base = snapshot_download(BASE[0], revision=BASE[1], allow_patterns=[
    'model_index.json', 'scheduler/*', 'tokenizer/*', 'tokenizer_2/*', '*/config.json',
    'text_encoder/model.fp16.safetensors', 'text_encoder_2/model.fp16.safetensors',
    'unet/diffusion_pytorch_model.fp16.safetensors'])
pipe = StableDiffusionXLControlNetImg2ImgPipeline.from_pretrained(
    pasta_base, vae=vae, controlnet=controlnet, torch_dtype=dtype, variant='fp16')
pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
pipe.to('cuda')
pipe.set_progress_bar_config(disable=True)
t_carga = round(time.time() - t_carga, 1)


def posicoes(total, lado, sobra):
    if total <= lado:
        return [0]
    n = math.ceil((total - lado) / (lado - sobra)) + 1
    return [round(i * (total - lado) / (n - 1)) for i in range(n)]


def rampa(n, antes, depois):
    """Peso 1D de um ladrilho: sobe na sobreposição com o vizinho anterior e desce na do seguinte."""
    w = np.ones(n, np.float32)
    if antes:
        w[:antes] = (np.arange(antes, dtype=np.float32) + 0.5) / antes
    if depois:
        w[n - depois:] = np.minimum(w[n - depois:], (depois - np.arange(depois, dtype=np.float32) - 0.5) / depois)
    return w


def baixar(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'grp-ampliador/1.0'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def ampliar(img, escala, forca, detalhe, passos, semente, prompt, guia=7.0, fidelidade=0.3, contraste=1.5):
    W0, H0 = img.size
    W, H = round(W0 * escala), round(H0 * escala)
    base = img.resize((W, H), Image.LANCZOS) if (W, H) != (W0, H0) else img
    ref = np.asarray(base, np.float32)
    acc = np.zeros((H, W, 3), np.float32)
    peso = np.zeros((H, W, 1), np.float32)
    xs, ys = posicoes(W, LADO, SOBRA), posicoes(H, LADO, SOBRA)
    for yi, y in enumerate(ys):
        for xi, x in enumerate(xs):
            lw, lh = min(LADO, W), min(LADO, H)
            pedaco = base.crop((x, y, x + lw, y + lh))
            g = torch.Generator('cuda').manual_seed(semente)  # mesma semente: o "grão" casa entre ladrilhos
            saida = pipe(prompt=prompt, negative_prompt=NEGATIVE, image=pedaco, control_image=pedaco,
                         strength=forca, num_inference_steps=passos, guidance_scale=guia,
                         controlnet_conditioning_scale=detalhe, generator=g).images[0]
            ax = (xs[xi - 1] + lw - x) if xi > 0 else 0
            dx = (x + lw - xs[xi + 1]) if xi < len(xs) - 1 else 0
            ay = (ys[yi - 1] + lh - y) if yi > 0 else 0
            dy = (y + lh - ys[yi + 1]) if yi < len(ys) - 1 else 0
            w = (rampa(lh, ay, dy)[:, None] * rampa(lw, ax, dx)[None, :])[..., None]
            acc[y:y + lh, x:x + lw] += np.asarray(saida, np.float32) * w
            peso[y:y + lh, x:x + lw] += w
    res = acc / np.maximum(peso, 1e-6)
    # Luz e cor do render + detalhe da IA, com o contraste local de volta (recombinar.py)
    out = recombinar(res, ref, escala, fidelidade=fidelidade, ganho_max=contraste, dispositivo='cuda')
    return Image.fromarray(out), len(xs) * len(ys)


def handler(job):
    e = job['input']
    for k in ('imagem_url', 'upload_url'):
        if not str(e.get(k, '')).startswith('https://'):
            return {'error': f'{k} precisa ser um link https'}
    escala = float(min(max(float(e.get('escala', 1.5)), 1.0), 2.0))
    forca = float(min(max(float(e.get('forca', 0.5)), 0.15), 0.65))
    detalhe = float(min(max(float(e.get('detalhe', 0.6)), 0.5), 1.0))
    fidelidade = float(min(max(float(e.get('fidelidade', 0.3)), 0.0), 1.0))
    contraste = float(min(max(float(e.get('contraste', 1.5)), 1.0), 2.0))
    passos = int(min(max(int(e.get('passos', 30)), 12), 60))
    guia = float(min(max(float(e.get('guia', 7.0)), 2.0), 10.0))
    semente = int(e.get('semente', 20261110))
    prompt = PROMPT + (', ' + str(e['prompt_extra'])[:300] if e.get('prompt_extra') else '')

    t0 = time.time()
    img = Image.open(io.BytesIO(baixar(e['imagem_url']))).convert('RGB')
    if img.size[0] * escala > 8192 or img.size[1] * escala > 8192:
        return {'error': 'resultado passaria de 8192 px'}
    with torch.inference_mode():
        out, ladrilhos = ampliar(img, escala, forca, detalhe, passos, semente, prompt, guia, fidelidade, contraste)
    buf = io.BytesIO()
    out.save(buf, 'JPEG', quality=95, subsampling=0)
    dados = buf.getvalue()
    info = {'ampliador': True, 'escala': escala, 'forca': forca, 'detalhe': detalhe, 'passos': passos, 'guia': guia,
            'fidelidade': fidelidade, 'contraste': contraste,
            'ladrilhos': ladrilhos, 'carga_modelos_s': t_carga, 'total_s': round(time.time() - t0, 1),
            'resolucao': list(out.size), 'device': torch.cuda.get_device_name(0)}
    req = urllib.request.Request(e['upload_url'], data=dados, method='PUT',
                                 headers={'Content-Type': 'image/jpeg', 'X-Resumo': json.dumps(info)})
    urllib.request.urlopen(req, timeout=300).close()
    return dict(info, bytes=len(dados), enviado=True)


runpod.serverless.start({'handler': handler})
