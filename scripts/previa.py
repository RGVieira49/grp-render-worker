# Versão leve do modelo para o visualizador 3D do Estúdio (escolher a câmera no navegador).
# blender -b modelo.blend -P previa.py -- saida.glb [faces_por_objeto]
# Grava também <saida sem .glb>.materiais_info.json: classe (mesmas regras do render), tamanho real de uma
# repetição da textura (m) e cor média de cada material — é o que o painel de materiais usa.
# Mesma limpeza do render (peças soltas fora), malhas pesadas simplificadas, texturas em no máx. 512 px,
# Draco. As coordenadas são as mesmas do render: o que se enquadra aqui é o que a GPU renderiza.
import bpy, sys, json, os, tempfile, time
from mathutils import Vector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regras_materiais import classe_pelo_nome  # noqa: E402

args = sys.argv[sys.argv.index('--') + 1:]
saida = args[0]
alvo_faces = int(args[1]) if len(args) > 1 else 3000
t0 = time.time()
sc = bpy.context.scene

malhas = [o for o in sc.objects if o.type == 'MESH']
xs = sorted((o.matrix_world @ Vector(o.bound_box[0])).x for o in malhas)
ys = sorted((o.matrix_world @ Vector(o.bound_box[0])).y for o in malhas)
mx_, my_ = xs[len(xs) // 2], ys[len(ys) // 2]
fora = 0
for o in malhas:
    c = o.matrix_world @ (sum((Vector(v) for v in o.bound_box), Vector()) / 8)
    tira = abs(c.x - mx_) > 15 or abs(c.y - my_) > 15
    tira = tira or any(s.material and 'light beam' in s.material.name.lower() for s in o.material_slots)
    if tira:
        bpy.data.objects.remove(o, do_unlink=True)
        fora += 1
malhas = [o for o in sc.objects if o.type == 'MESH']

simplificadas = 0
for o in malhas:
    n = len(o.data.polygons)
    if n > alvo_faces:
        d = o.modifiers.new('leve', 'DECIMATE')
        d.ratio = max(0.04, alvo_faces / n)
        d.use_collapse_triangulate = True
        simplificadas += 1

# Texturas: no máx. 512 px, gravadas como JPEG num diretório temporário (o exportador do glTF reaproveita
# o arquivo embutido original se a imagem continuar "empacotada", por isso ela passa a apontar para o novo).
# Ficha dos materiais (antes de reduzir as texturas: a cor média sai da imagem original)
def metros_por_repeticao(m):
    amostras = []
    for o in malhas:
        if not o.data.uv_layers or not any(s.material == m for s in o.material_slots):
            continue
        idx = {i for i, s in enumerate(o.material_slots) if s.material == m}
        uv = o.data.uv_layers.active.data
        mw = o.matrix_world
        for p in o.data.polygons:
            if p.material_index not in idx or len(p.loop_indices) < 3:
                continue
            l = p.loop_indices
            a = mw @ o.data.vertices[o.data.loops[l[0]].vertex_index].co
            b = mw @ o.data.vertices[o.data.loops[l[1]].vertex_index].co
            d3, d2 = (a - b).length, (uv[l[0]].uv - uv[l[1]].uv).length
            if d3 > 0.05 and d2 > 1e-6:
                amostras.append(d3 / d2)
            if len(amostras) >= 40:
                break
        if len(amostras) >= 40:
            break
    return sorted(amostras)[len(amostras) // 2] if amostras else None


def cor_media(m):
    pr = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) if m.use_nodes else None
    if not pr:
        return None
    if pr.inputs['Base Color'].is_linked:
        t = pr.inputs['Base Color'].links[0].from_node
        if t.type == 'TEX_IMAGE' and t.image and t.image.size[0]:
            try:
                px4 = t.image.pixels[:]  # valores como gravados (sRGB, 4 canais)
                n = len(px4) // 4
                passo = max(1, n // 2000)
                soma = [0.0, 0.0, 0.0]
                k = 0
                for i in range(0, n, passo):
                    soma[0] += px4[4 * i]; soma[1] += px4[4 * i + 1]; soma[2] += px4[4 * i + 2]; k += 1
                return '#%02x%02x%02x' % tuple(int(max(0, min(1, v / k)) * 255) for v in soma)
            except (RuntimeError, IndexError):
                return None
        return None
    c = pr.inputs['Base Color'].default_value
    lin = [c[0], c[1], c[2]]
    srgb = [12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055 for v in lin]
    return '#%02x%02x%02x' % tuple(int(max(0, min(1, v)) * 255) for v in srgb)


usados = {s.material for o in malhas for s in o.material_slots if s.material}
ficha = {}
for m in usados:
    ficha[m.name] = {'classe': classe_pelo_nome(m.name), 'metros': round(metros_por_repeticao(m) or 0, 3) or None,
                     'textura': bool(m.use_nodes and any(n.type == 'TEX_IMAGE' and n.image for n in m.node_tree.nodes)),
                     'cor': cor_media(m)}
with open(os.path.splitext(saida)[0] + '.materiais_info.json', 'w') as f:
    json.dump(ficha, f, ensure_ascii=False)

pasta = tempfile.mkdtemp()
ruins = set()
for i, im in enumerate(list(bpy.data.images)):
    if im.source != 'FILE' or im.size[0] == 0:
        continue
    k = min(1.0, 512 / max(im.size))
    try:
        if k < 1:
            im.scale(max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k)))
        destino = os.path.join(pasta, f'{i}.jpg')
        im.file_format = 'JPEG'
        im.save(filepath=destino, quality=72)
        if im.packed_file:
            im.unpack(method='REMOVE')
        im.filepath = destino
        im.reload()
    except RuntimeError:
        ruins.add(im.name)

# Textura que não pôde ser gravada sai do material (senão o glTF fica com uma textura "sem imagem"
# e o navegador desiste do modelo inteiro). O material fica com a cor lisa.
sem_textura = 0
for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    for n in list(m.node_tree.nodes):
        if n.type == 'TEX_IMAGE' and (n.image is None or n.image.name in ruins):
            m.node_tree.nodes.remove(n)
            sem_textura += 1

bpy.ops.export_scene.gltf(
    filepath=saida, export_format='GLB', export_apply=True, export_yup=True,
    export_image_format='WEBP', export_image_quality=70,  # com JPEG, texturas com alfa saem em PNG (pesado)
    export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
    export_draco_position_quantization=12, export_draco_normal_quantization=8, export_draco_texcoord_quantization=10,
    export_cameras=False, export_lights=False, export_animations=False,
)
# Conferência final do .glb: o exportador às vezes cria uma cópia de imagem que não consegue gravar
# (canal alfa usado à parte) e deixa a textura "sem imagem". O three.js não aceita isso e desiste do
# modelo inteiro; aqui, toda referência a textura sem imagem sai do material (fica a cor lisa).
def consertar_glb(caminho):
    import struct
    dados = open(caminho, 'rb').read()
    tam_json = struct.unpack('<I', dados[12:16])[0]
    j = json.loads(dados[20:20 + tam_json])
    texturas = j.get('textures', [])
    ruins_t = {i for i, t in enumerate(texturas)
               if 'source' not in t and not any(isinstance(v, dict) and 'source' in v for v in (t.get('extensions') or {}).values())}
    if not ruins_t:
        return 0
    def limpar(o):
        n = 0
        if isinstance(o, dict):
            for k in list(o):
                v = o[k]
                if k.endswith('Texture') and isinstance(v, dict) and v.get('index') in ruins_t:
                    del o[k]
                    n += 1
                else:
                    n += limpar(v)
        elif isinstance(o, list):
            n += sum(limpar(v) for v in o)
        return n
    n = limpar(j.get('materials', []))
    novo = json.dumps(j, separators=(',', ':')).encode()
    novo += b' ' * ((4 - len(novo) % 4) % 4)
    resto = dados[20 + tam_json:]
    corpo = struct.pack('<I', len(novo)) + b'JSON' + novo + resto
    with open(caminho, 'wb') as f:
        f.write(dados[:8] + struct.pack('<I', 12 + len(corpo)) + corpo)
    return n


referencias_soltas = consertar_glb(saida)
print('PREVIA', json.dumps({'referencias_soltas': referencias_soltas, 'objetos': len(malhas), 'removidos': fora, 'simplificadas': simplificadas,
                            'texturas_descartadas': sem_textura, 'materiais': len(ficha),
                            'segundos': round(time.time() - t0, 1)}))
