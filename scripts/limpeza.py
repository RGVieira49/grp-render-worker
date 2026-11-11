# Peças soltas longe do modelo (o SketchUp às vezes exporta um objeto perdido a dezenas de metros, que
# estica a caixa do modelo e estraga o enquadramento). Usado pelo render e pela versão leve, para que os dois
# descartem exatamente as mesmas peças — o que se enquadra no navegador é o que a GPU renderiza.
#
# O centro é a mediana dos cantos mínimos de cada peça (resiste a peças perdidas). O raio cresce com o
# modelo: 15 m bastam para um decorado, mas um prédio inteiro ou uma área de lazer passam disso; aí o raio
# vira 2,5× a distância em que estão 90% das peças. Peça perdida continua muito além desse raio.
from mathutils import Vector

RAIO_MINIMO_M = 15.0
FATOR = 2.5


def _centro(o):
    return o.matrix_world @ (sum((Vector(v) for v in o.bound_box), Vector()) / 8)


def pecas_soltas(malhas):
    """Devolve (pecas_fora, raio_m)."""
    if not malhas:
        return [], RAIO_MINIMO_M
    xs = sorted((o.matrix_world @ Vector(o.bound_box[0])).x for o in malhas)
    ys = sorted((o.matrix_world @ Vector(o.bound_box[0])).y for o in malhas)
    mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
    dist = []
    for o in malhas:
        c = _centro(o)
        dist.append((max(abs(c.x - mx), abs(c.y - my)), o))
    ordem = sorted(d for d, _ in dist)
    p90 = ordem[int(0.9 * (len(ordem) - 1))]
    raio = max(RAIO_MINIMO_M, FATOR * p90)
    return [o for d, o in dist if d > raio], raio
