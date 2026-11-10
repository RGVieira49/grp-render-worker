"""Trabalhador RunPod: recebe o link do modelo e a cena, renderiza no Blender (Cycles/OptiX) e devolve a imagem.

Entrada do job:
    modelo_url     link do .glb (ou .blend) — baixado uma vez e guardado em cache
    modelo_sha256  opcional; chave do cache e conferência do download
    cena           câmera, sol, resolução, amostras… (o mesmo JSON de scripts/render_cena.py, sem "saida")
    formato        "jpg" (padrão, volta em base64) ou "png"
    upload_url     opcional; se vier, o arquivo final vai por PUT para esse link (URL assinada)

Na cena, "paisagem" escolhe o que aparece pelas janelas: "jardim" (padrão) ou "ceu".
"""
import base64, hashlib, json, os, re, shutil, subprocess, tempfile, time, urllib.request

import runpod

BLENDER = '/opt/blender/blender'
SCRIPTS = '/app/scripts'
# fundos fotográficos CC0 embutidos na imagem; a cena escolhe pelo nome, nunca por caminho
PAISAGENS = {'jardim': '/opt/ceu/jardim_4k.hdr', 'ceu': '/opt/ceu/ceu_4k.hdr'}
# volume de rede da RunPod quando montado; senão, disco do próprio worker
CACHE = '/runpod-volume/modelos' if os.path.isdir('/runpod-volume') else '/tmp/modelos'


def baixar(url, destino, sha_esperado=None):
    h = hashlib.sha256()
    tmp = destino + '.parcial'
    with urllib.request.urlopen(url, timeout=600) as r, open(tmp, 'wb') as f:
        while bloco := r.read(1 << 20):
            h.update(bloco)
            f.write(bloco)
    if sha_esperado and h.hexdigest() != sha_esperado:
        os.remove(tmp)
        raise ValueError(f'sha256 do modelo não confere: {h.hexdigest()}')
    os.replace(tmp, destino)
    return h.hexdigest()


def preparar_modelo(url, sha):
    """Devolve o caminho de um .blend pronto, usando o cache quando possível."""
    os.makedirs(CACHE, exist_ok=True)
    chave = sha or hashlib.sha256(url.split('?')[0].encode()).hexdigest()
    blend = os.path.join(CACHE, f'{chave}.blend')
    if os.path.exists(blend):
        return blend, True
    ext = '.blend' if url.split('?')[0].lower().endswith('.blend') else '.glb'
    bruto = os.path.join(CACHE, f'{chave}{ext}')
    baixar(url, bruto, sha)
    if ext == '.blend':
        os.replace(bruto, blend)
        return blend, False
    parcial = blend + '.parcial.blend'
    rodar([BLENDER, '-b', '--factory-startup', '-P', f'{SCRIPTS}/importar.py', '--', bruto, parcial])
    os.replace(parcial, blend)
    os.remove(bruto)
    return blend, False


def rodar(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f'Blender falhou ({p.returncode}): {p.stdout[-1500:]}{p.stderr[-1500:]}')
    return p.stdout


def handler(job):
    e = job['input']
    if not isinstance(e.get('modelo_url'), str) or not e['modelo_url'].startswith('https://'):
        return {'error': 'modelo_url precisa ser um link https'}
    cena = e.get('cena')
    if not isinstance(cena, dict) or 'camera' not in cena or 'resolucao' not in cena:
        return {'error': 'cena precisa de camera e resolucao'}
    w, h = cena['resolucao']
    if not (64 <= w <= 8192 and 64 <= h <= 8192) or not (1 <= int(cena.get('amostras', 128)) <= 4096):
        return {'error': 'resolucao ou amostras fora do limite'}
    formato = 'png' if e.get('formato') == 'png' else 'jpg'
    if e.get('upload_url') and not str(e['upload_url']).startswith('https://'):
        return {'error': 'upload_url precisa ser um link https'}

    t0 = time.time()
    blend, em_cache = preparar_modelo(e['modelo_url'], e.get('modelo_sha256'))
    t1 = time.time()

    pasta = tempfile.mkdtemp()
    try:
        saida = os.path.join(pasta, f'render.{formato}')
        cena = dict(cena, saida=saida, formato=formato.upper().replace('JPG', 'JPEG'))
        cena['ceu_foto'] = PAISAGENS.get(cena.pop('paisagem', 'jardim'), PAISAGENS['jardim'])
        arq = os.path.join(pasta, 'cena.json')
        with open(arq, 'w') as f:
            json.dump(cena, f)
        log = rodar([BLENDER, '-b', blend, '-P', f'{SCRIPTS}/render_cena.py', '--', arq])
        resumo = json.loads(re.search(r'^RESUMO (.*)$', log, re.M).group(1))
        with open(saida, 'rb') as f:
            dados = f.read()
        res = {'formato': formato, 'bytes': len(dados), 'modelo_em_cache': em_cache,
               'preparo_modelo_s': round(t1 - t0, 1), 'total_s': round(time.time() - t0, 1), 'resumo': resumo}
        if e.get('upload_url'):
            req = urllib.request.Request(e['upload_url'], data=dados, method='PUT',
                                         headers={'Content-Type': 'image/png' if formato == 'png' else 'image/jpeg'})
            urllib.request.urlopen(req, timeout=300).close()
            res['enviado'] = True
        else:
            res['imagem_base64'] = base64.b64encode(dados).decode()
        return res
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


runpod.serverless.start({'handler': handler})
