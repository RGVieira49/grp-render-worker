"""Junta o resultado da IA com o render original, por faixas de detalhe (testável sem GPU).

Três faixas, do maior para o menor:
  luz e cor   (formas maiores que ~1/16 da imagem)  → sempre do render: a IA não mexe na iluminação;
  meio-tom    (formas de alguns pixels até ~1/16)   → mistura render × IA (``fidelidade``);
  micro       (trama, veio, poros, bordas finas)    → da IA: é o ganho de foto.

Por fim, o contraste local: a IA tende a "lavar" a textura (trama mais fina, porém mais clara e chapada;
foi o véu do teste de 11/11, −11% de contraste no quadro). Em cada região, o detalhe total volta a ter
a força do render (o ganho fica entre 1× e ``ganho_max``), sem perder o desenho novo da IA.
"""
import numpy as np
import torch
import torch.nn.functional as F
import torchvision.transforms.functional as TF


def _t(a, dispositivo):
    return torch.from_numpy(np.ascontiguousarray(a)).permute(2, 0, 1)[None].to(dispositivo)


def _baixas(t, fator):
    """Só a luz e a cor 'grandes': reduz 1/fator, suaviza e volta ao tamanho."""
    H, W = t.shape[-2:]
    p = F.interpolate(t, size=(max(1, H // fator), max(1, W // fator)), mode='area')
    p = TF.gaussian_blur(p, [5, 5], [1.0, 1.0])
    return F.interpolate(p, size=(H, W), mode='bicubic', align_corners=False)


def _suave(t, sigma):
    k = int(sigma * 3) * 2 + 1
    return TF.gaussian_blur(t, [k, k], [sigma, sigma])


def _energia(d, fator):
    """Força média do detalhe numa vizinhança (desvio quadrático local, em luminância)."""
    lum = d.mean(dim=1, keepdim=True)
    return _baixas(lum * lum, fator).clamp_min(0).sqrt()


def recombinar(ia, render, escala, fidelidade=0.3, micro=1.0, ganho_max=1.5, dispositivo='cpu'):
    """ia, render: float32 H×W×3 (0–255), mesmo tamanho. Devolve uint8 H×W×3."""
    fator = max(8, round(16 * escala))
    sigma = 1.5 * escala  # abaixo disso é "micro"
    with torch.inference_mode():
        a, r = _t(ia, dispositivo), _t(render, dispositivo)
        a_s, r_s = _suave(a, sigma), _suave(r, sigma)
        a_b, r_b = _baixas(a, fator), _baixas(r, fator)
        detalhe = (1 - fidelidade) * (a_s - a_b) + fidelidade * (r_s - r_b) + micro * (a - a_s)
        del a_s, r_s, a_b
        if ganho_max > 1:
            janela = max(4, fator // 3)  # regiões de ~8 px na 6K: segue a textura de cada material
            ganho = (_energia(r - r_b, janela) / _energia(detalhe, janela).clamp_min(0.5)).clamp(1.0, ganho_max)
            detalhe = detalhe * ganho
        saida = r_b + detalhe
        saida = saida[0].permute(1, 2, 0).clamp(0, 255).add(0.5).floor().to(torch.uint8).cpu().numpy()
    return saida
