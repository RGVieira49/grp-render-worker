# Enxuga o .glb do SketchUp antes de o Blender importar, sem mudar nada do que se vê.
#     python3 enxugar.py entrada.glb saida.glb      (imprime "ENXUGADO {json}"; código 3 = nada a fazer)
#
# Por quê: o SketchUp exporta cada componente repetido (cadeira, esquadria, luminária...) como uma cópia
# inteira da geometria, debaixo de uma árvore enorme de grupos. Num anteprojeto de 635 MB eram 198.498 nós
# e 26.268 malhas, nenhuma reaproveitada: o importador do Blender levava quase 1 h e o job da GPU morria
# no limite de 30 min (e nada ficava no cache, então "tentar de novo" falhava igual).
#
# O que faz, mexendo só no JSON e no bloco binário (nenhuma matriz é decomposta nem arredondada):
#   1. geometria idêntica byte a byte vira uma só, usada por todas as cópias (accessors e depois malhas);
#   2. cada peça cuja posição final não tem cisalhamento sobe para a raiz da cena com a matriz final (float64);
#      as que têm (componente esticado dentro de um grupo girado: almofada, folha de planta), ou que estão sob
#      um grupo com cisalhamento na própria matriz, continuam na árvore original, intactas;
#   3. grupos que ficaram vazios saem; o binário é regravado só com o que ainda é usado.
# Materiais, nomes de material, texturas, UVs e cores ficam exatamente como vieram (os ajustes do arquiteto
# e as texturas físicas do render dependem disso).
import hashlib, json, math, struct, sys, time

TAM = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
COMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}


def ler_glb(caminho):
    with open(caminho, 'rb') as f:
        dados = f.read()
    if dados[:4] != b'glTF':
        raise ValueError('não é .glb')
    p, j, binario = 12, None, b''
    while p < len(dados):
        n, tipo = struct.unpack('<I4s', dados[p:p + 8])
        bloco = dados[p + 8:p + 8 + n]
        if tipo == b'JSON':
            j = json.loads(bloco)
        elif tipo == b'BIN\x00':
            binario = bloco
        p += 8 + n
    return j, binario


def gravar_glb(caminho, j, binario):
    js = json.dumps(j, separators=(',', ':'), ensure_ascii=False).encode()
    js += b' ' * (-len(js) % 4)
    binario += b'\x00' * (-len(binario) % 4)
    corpo = struct.pack('<I4s', len(js), b'JSON') + js + struct.pack('<I4s', len(binario), b'BIN\x00') + binario
    with open(caminho, 'wb') as f:
        f.write(b'glTF' + struct.pack('<II', 2, 12 + len(corpo)) + corpo)


# ------------------------------------------------------------------ matrizes 4x4 (coluna a coluna, como no glTF)
def mat_local(nd):
    if 'matrix' in nd:
        return [float(v) for v in nd['matrix']]
    tx, ty, tz = nd.get('translation', [0, 0, 0])
    x, y, z, w = nd.get('rotation', [0, 0, 0, 1])
    sx, sy, sz = nd.get('scale', [1, 1, 1])
    r = [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w),
         2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w),
         2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]
    return [r[0] * sx, r[1] * sx, r[2] * sx, 0, r[3] * sy, r[4] * sy, r[5] * sy, 0,
            r[6] * sz, r[7] * sz, r[8] * sz, 0, tx, ty, tz, 1]


def mult(a, b):
    return [sum(a[k * 4 + i] * b[c * 4 + k] for k in range(4)) for c in range(4) for i in range(4)]


def sem_cisalhamento(m):
    col = [m[0:3], m[4:7], m[8:11]]
    nor = [math.sqrt(sum(v * v for v in c)) for c in col]
    if min(nor) < 1e-12 or abs(m[3]) + abs(m[7]) + abs(m[11]) > 1e-12 or abs(m[15] - 1) > 1e-12:
        return False
    return all(abs(sum(col[a][i] * col[b][i] for i in range(3))) / (nor[a] * nor[b]) < 1e-6 for a, b in ((0, 1), (0, 2), (1, 2)))


def tortos(j):
    """Nós com cisalhamento na própria matriz ou na de algum grupo acima."""
    nos, r = j.get('nodes', []), set()
    pilha = [(n, False) for s in j.get('scenes', []) for n in s.get('nodes', [])]
    while pilha:
        n, torto = pilha.pop()
        torto = torto or not sem_cisalhamento(mat_local(nos[n]))
        if torto:
            r.add(n)
        pilha.extend((c, torto) for c in nos[n].get('children', []))
    return r


# ------------------------------------------------------------------ 1. geometria repetida
def dedup(j, binario):
    vistas = j.get('bufferViews', [])
    acc_novo, chave_acc = {}, {}
    for i, a in enumerate(j.get('accessors', [])):
        if 'bufferView' not in a or 'sparse' in a:
            acc_novo[i] = i
            continue
        v = vistas[a['bufferView']]
        if v.get('buffer', 0) != 0:
            acc_novo[i] = i
            continue
        elem = TAM[a['componentType']] * COMP[a['type']]
        passo = v.get('byteStride') or elem
        ini = v.get('byteOffset', 0) + a.get('byteOffset', 0)
        fim = ini + passo * (a['count'] - 1) + elem if a['count'] else ini
        h = hashlib.sha256(binario[ini:fim]).hexdigest()
        k = (a['componentType'], a['type'], a['count'], bool(a.get('normalized')), passo, v.get('target'), h)
        acc_novo[i] = chave_acc.setdefault(k, i)

    def troca_prim(p):
        p = dict(p)
        p['attributes'] = {k: acc_novo[v] for k, v in p['attributes'].items()}
        if 'indices' in p:
            p['indices'] = acc_novo[p['indices']]
        if 'targets' in p:
            p['targets'] = [{k: acc_novo[v] for k, v in t.items()} for t in p['targets']]
        return p

    # peça com cisalhamento na própria matriz ou na de algum grupo acima: o importador do Blender grava a
    # deformação nos vértices, o que só dá certo se a malha for só dela (ex.: almofadas e um caule de arranjo)
    nos = j.get('nodes', [])
    proprias = {nos[n]['mesh'] for n in tortos(j) if 'mesh' in nos[n]}
    malha_nova, chave_malha, malhas = {}, {}, j.get('meshes', [])
    for i, m in enumerate(malhas):
        m['primitives'] = [troca_prim(p) for p in m['primitives']]
        k = i if i in proprias else json.dumps({x: m[x] for x in m if x != 'name'}, sort_keys=True)
        malha_nova[i] = chave_malha.setdefault(k, i)
    for nd in j.get('nodes', []):
        if 'mesh' in nd:
            nd['mesh'] = malha_nova[nd['mesh']]
    return len(set(malha_nova.values()))


# ------------------------------------------------------------------ 2. árvore de grupos
def achatar(j):
    nos = j.get('nodes', [])
    if j.get('skins') or j.get('animations') or not nos:
        return 0
    cena = j['scenes'][j.get('scene', 0)]
    # percorre na mesma ordem do importador (pré-ordem): a ordem das peças na cena do Blender fica igual,
    # e o que depende dela (amostragem da ficha de materiais) sai idêntico
    pai, mundo, ordem = {}, {}, []
    pilha = [(n, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]) for n in reversed(cena['nodes'])]
    while pilha:
        n, m_pai = pilha.pop()
        if n in mundo:  # nó usado duas vezes: não mexe em nada
            return 0
        mundo[n] = mult(m_pai, mat_local(nos[n]))
        ordem.append(n)
        for c in reversed(nos[n].get('children', [])):
            pai[c] = n
            pilha.append((c, mundo[n]))
    # Peça sem cisalhamento vai para a raiz com a matriz final. Peça com cisalhamento leva uma cópia da cadeia
    # de grupos acima dela, na mesma posição da ordem; peças vizinhas reaproveitam a cadeia já aberta.
    torto, novos, raiz, cadeia, na_arvore = tortos(j), [], [], [], 0
    for n in ordem:
        nd = nos[n]
        if 'mesh' not in nd and 'camera' not in nd:
            continue
        if n not in torto and sem_cisalhamento(mundo[n]):
            plano = {k: v for k, v in nd.items() if k not in ('children', 'matrix', 'translation', 'rotation', 'scale')}
            plano['matrix'] = mundo[n]
            raiz.append(len(novos))
            novos.append(plano)
            cadeia = []
            continue
        caminho = [n]
        while pai.get(caminho[-1]) is not None:
            caminho.append(pai[caminho[-1]])
        caminho.reverse()
        comum = 0
        while comum < min(len(cadeia), len(caminho) - 1) and cadeia[comum][0] == caminho[comum]:
            comum += 1
        cadeia = cadeia[:comum]
        for k in caminho[comum:]:
            copia = {x: v for x, v in nos[k].items() if x != 'children'}
            if k != n:  # grupo acima: a peça dele (se tiver) já saiu antes, na ordem
                copia.pop('mesh', None)
                copia.pop('camera', None)
            i = len(novos)
            novos.append(copia)
            if cadeia:
                novos[cadeia[-1][1]].setdefault('children', []).append(i)
            else:
                raiz.append(i)
            cadeia.append((k, i))
        na_arvore += 1
    j['nodes'] = novos
    for s in j['scenes']:
        s['nodes'] = raiz if s is cena else []
    return na_arvore


# ------------------------------------------------------------------ 3. binário só com o que é usado
def compactar(j, binario):
    usados = set()
    malhas_usadas = sorted({nd['mesh'] for nd in j['nodes'] if 'mesh' in nd})
    mapa_m = {o: i for i, o in enumerate(malhas_usadas)}
    j['meshes'] = [j['meshes'][o] for o in malhas_usadas]
    for nd in j['nodes']:
        if 'mesh' in nd:
            nd['mesh'] = mapa_m[nd['mesh']]
    acc_usados = sorted({a for m in j['meshes'] for p in m['primitives']
                         for a in list(p['attributes'].values()) + ([p['indices']] if 'indices' in p else [])
                         + [x for t in p.get('targets', []) for x in t.values()]})
    mapa_a = {o: i for i, o in enumerate(acc_usados)}
    j['accessors'] = [j['accessors'][o] for o in acc_usados]
    for m in j['meshes']:
        for p in m['primitives']:
            p['attributes'] = {k: mapa_a[v] for k, v in p['attributes'].items()}
            if 'indices' in p:
                p['indices'] = mapa_a[p['indices']]
            if 'targets' in p:
                p['targets'] = [{k: mapa_a[v] for k, v in t.items()} for t in p['targets']]
    for a in j['accessors']:
        if 'bufferView' in a:
            usados.add(a['bufferView'])
        if 'sparse' in a:
            usados.add(a['sparse']['indices']['bufferView'])
            usados.add(a['sparse']['values']['bufferView'])
    for im in j.get('images', []):
        if 'bufferView' in im:
            usados.add(im['bufferView'])
    vistas = j.get('bufferViews', [])
    if any(v.get('buffer', 0) != 0 for v in vistas):
        raise ValueError('mais de um buffer')
    partes, mapa_v, novas, pos = [], {}, [], 0
    for o in sorted(usados):
        v = dict(vistas[o])
        ini = v.get('byteOffset', 0)
        bloco = binario[ini:ini + v['byteLength']]
        pad = -pos % 8
        partes.append(b'\x00' * pad)
        pos += pad
        v['byteOffset'] = pos
        partes.append(bloco)
        pos += len(bloco)
        mapa_v[o] = len(novas)
        novas.append(v)
    j['bufferViews'] = novas
    for a in j['accessors']:
        if 'bufferView' in a:
            a['bufferView'] = mapa_v[a['bufferView']]
        if 'sparse' in a:
            for k in ('indices', 'values'):
                a['sparse'][k]['bufferView'] = mapa_v[a['sparse'][k]['bufferView']]
    for im in j.get('images', []):
        if 'bufferView' in im:
            im['bufferView'] = mapa_v[im['bufferView']]
    novo = b''.join(partes)
    j['buffers'] = [{'byteLength': len(novo)}]
    return novo


def enxugar(entrada, saida):
    t0 = time.time()
    j, binario = ler_glb(entrada)
    if len(j.get('buffers', [])) != 1 or 'uri' in j['buffers'][0]:
        return None
    antes = {'nos': len(j.get('nodes', [])), 'malhas': len(j.get('meshes', [])), 'mb': round(len(binario) / 1e6, 1)}
    dedup(j, binario)
    na_arvore = achatar(j)
    binario = compactar(j, binario)
    gravar_glb(saida, j, binario)
    return {'antes': antes, 'depois': {'nos': len(j['nodes']), 'malhas': len(j['meshes']), 'mb': round(len(binario) / 1e6, 1)},
            'pecas_mantidas_na_arvore': na_arvore, 'segundos': round(time.time() - t0, 1)}


if __name__ == '__main__':
    r = enxugar(sys.argv[1], sys.argv[2])
    if r is None:
        sys.exit(3)
    print('ENXUGADO ' + json.dumps(r))
