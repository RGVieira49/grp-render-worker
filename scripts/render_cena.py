# Render de uma vista: blender -b modelo.blend -P render_cena.py -- cena.json
# A cena.json traz câmera, luz, resolução e amostras. O mesmo script roda no Mac (Metal) e na GPU alugada (OptiX).
# v4: texturas finas (CC0) no lugar das texturas pequenas do SketchUp, vidro que deixa a luz passar,
#     exposição e balanço de branco medidos numa prévia (como o fotômetro da câmera), presets de luz e 4K.
import bpy, sys, os, json, math, re, time, tempfile
import numpy as np
from mathutils import Vector

cfg = json.load(open(sys.argv[sys.argv.index('--') + 1]))
sc = bpy.context.scene
t0 = time.time()
TEXTURAS = cfg.get('texturas_dir', '/opt/texturas')

# ---------------------------------------------------------------- 1. limpeza
malhas = [o for o in sc.objects if o.type == 'MESH']
xs = sorted((o.matrix_world @ Vector(o.bound_box[0])).x for o in malhas)
ys = sorted((o.matrix_world @ Vector(o.bound_box[0])).y for o in malhas)
mx_, my_ = xs[len(xs) // 2], ys[len(ys) // 2]
for o in malhas:  # peças soltas longe do apartamento
    c = o.matrix_world @ (sum((Vector(v) for v in o.bound_box), Vector()) / 8)
    if abs(c.x - mx_) > 15 or abs(c.y - my_) > 15:
        o.hide_render = True

# Peças espelhadas no SketchUp chegam com as normais personalizadas invertidas (o render vê o avesso
# e pinta de preto). Sem a normal personalizada, o Blender recalcula a frente certa pela geometria.
espelhadas = 0
for o in malhas:
    if o.matrix_world.determinant() < 0 and 'custom_normal' in o.data.attributes:
        o.data.attributes.remove(o.data.attributes['custom_normal'])
        espelhadas += 1

# ---------------------------------------------------------------- 2. materiais por classe
# O SketchUp exporta tudo com rugosidade 0,5, sem metal e com texturas de 300–600 px. Cada material
# ganha uma classe (pelo nome, ou pela lista "materiais" da cena, que vence) e a classe diz o acabamento.
CLASSES = [
    (r'vidro|glass|translucent|cristal', 'vidro'),
    (r'espelho|mirror', 'espelho'),
    (r'a[çc]o\b|inox|cromad|chrome|metal|alum[ií]n|steel|lat[ãa]o|dourad|gold|bronze|cobre|niquel', 'metal'),
    (r'piso.*(lamin|madeira|vin[ií]l)|assoalho|parquet|taco', 'piso_madeira'),
    (r'madeira|wood|mdf|carvalho|freij[óo]|nogueira|louro|cumaru|ripad|amadeir|lamin|ver?niz|lâmina', 'madeira'),
    (r'boucl', 'boucle'),
    (r'suede|camur|veludo|velvet', 'suede'),
    (r'tapete|\brug\b|carpet|地毯', 'tapete'),
    (r'linho|tecido|fabric|algod|almofad|cortina|curtain|manta|len[çc]ol|sof[áa]|estofad', 'tecido'),
    (r'couro|leather', 'couro'),
    (r'queimado', 'cimento_queimado'),
    (r'porcelanat|m[áa]rmore|marble|granit|quartz|travertin|silestone|dekton|pedra', 'pedra'),
    (r'cer[âa]mic|azulej|\btile|pastilha|subway|decortiles|brut', 'ceramica'),
    (r'grafiato|textura|reboco|stucco|concreto|concrete|cimento', 'textura_parede'),
    (r'laca|laqu|lacad', 'laca'),
    (r'acr[ií]lic|pl[áa]stic', 'plastico'),
    (r'folha|leaf|planta|plant|vegeta', 'planta'),
    (r'pintura|tinta|suvinil|coral|parede|wall|gesso|forro|lume|color', 'pintura'),
]
# rugosidade base e relevo (tirado da própria textura) de cada classe
ACABAMENTO = {
    'vidro': dict(rug=0.02), 'espelho': dict(rug=0.02), 'metal': dict(rug=0.3),
    'piso_madeira': dict(rug=0.38), 'madeira': dict(rug=0.42, verniz=0.12),
    'boucle': dict(rug=0.95, brilho_tecido=0.5), 'suede': dict(rug=0.8, brilho_tecido=0.7),
    'tapete': dict(rug=0.95, brilho_tecido=0.4), 'tecido': dict(rug=0.92, brilho_tecido=0.4),
    'couro': dict(rug=0.45), 'cimento_queimado': dict(rug=0.55), 'pedra': dict(rug=0.14),
    'ceramica': dict(rug=0.2, relevo=0.05), 'textura_parede': dict(rug=0.85, relevo=0.06),
    'laca': dict(rug=0.22), 'plastico': dict(rug=0.3), 'planta': dict(rug=0.5, translucencia=0.25),
    'pintura': dict(rug=0.85), 'outro': dict(rug=0.6),
}
# Texturas físicas CC0 (Poly Haven) e o tamanho real de uma repetição, em metros.
# "trocar": a cor vem da textura fina (casada com a cor média do original);
# senão a cor do arquiteto fica e a textura fina entra só no relevo e no brilho.
FINAS = {
    'madeira': dict(base='oak_veneer_01', metros=1.83, trocar=True, normal=0.35),
    'piso_madeira': dict(base='laminate_floor_02', metros=1.70, trocar=True, normal=0.5),
    'tecido': dict(base='rough_linen', metros=0.27, trocar=False, normal=0.6),
    'tapete': dict(base='rough_linen', metros=0.18, trocar=False, normal=0.9),
    'boucle': dict(base='wool_boucle', metros=0.31, trocar=False, normal=0.9),
    'suede': dict(base='scuba_suede', metros=0.28, trocar=False, normal=0.5),
}
SOBREPOR = cfg.get('materiais', {})


def classe_de(nome):
    if nome in SOBREPOR:
        return SOBREPOR[nome]
    n = nome.lower()
    for padrao, c in CLASSES:
        if re.search(padrao, n):
            return c
    return 'outro'


_imagens, _medias = {}, {}
def imagem(nome, sufixo, cor):
    caminho = os.path.join(TEXTURAS, f'{nome}_{sufixo}.jpg')
    if caminho not in _imagens:
        im = bpy.data.images.load(caminho, check_existing=True)
        im.colorspace_settings.name = 'sRGB' if cor else 'Non-Color'
        _imagens[caminho] = im
    return _imagens[caminho]


def media_linear(im):
    """Cor média (linear) de uma textura, por amostragem: basta para casar o tom."""
    if im.name in _medias:
        return _medias[im.name]
    w, h = im.size
    px = np.empty(w * h * im.channels, np.float32)
    im.pixels.foreach_get(px)
    px = px.reshape(-1, im.channels)[::97, :3]
    if im.colorspace_settings.name == 'sRGB' and not im.is_float:
        px = np.where(px <= 0.04045, px / 12.92, ((px + 0.055) / 1.055) ** 2.4)
    _medias[im.name] = px.mean(0)
    return _medias[im.name]


def metros_por_repeticao(m):
    """Quantos metros do modelo uma repetição da textura original cobre (mediana de algumas faces)."""
    amostras = []
    for o in malhas:
        if not o.data.uv_layers or not any(s.material == m for s in o.material_slots):
            continue
        idx = {i for i, s in enumerate(o.material_slots) if s.material == m}
        uv = o.data.uv_layers.active.data
        mw = o.matrix_world
        for p in o.data.polygons:
            if p.material_index not in idx:
                continue
            l = p.loop_indices
            if len(l) < 3:
                continue
            a = mw @ o.data.vertices[o.data.loops[l[0]].vertex_index].co
            b = mw @ o.data.vertices[o.data.loops[l[1]].vertex_index].co
            d3, d2 = (a - b).length, (uv[l[0]].uv - uv[l[1]].uv).length
            if d3 > 0.05 and d2 > 1e-6:
                amostras.append(d3 / d2)
            if len(amostras) >= 40:
                break
        if len(amostras) >= 40:
            break
    if not amostras:
        return None
    amostras.sort()
    return amostras[len(amostras) // 2]


def coordenada(nt, tex_original, escala):
    """UV do material (a mesma orientação que o arquiteto deu) multiplicada pela escala da textura fina."""
    mapa = nt.nodes.new('ShaderNodeMapping')
    mapa.inputs['Scale'].default_value = (escala, escala, 1)
    if tex_original and tex_original.inputs['Vector'].is_linked:
        nt.links.new(tex_original.inputs['Vector'].links[0].from_socket, mapa.inputs['Vector'])
    else:
        uvn = nt.nodes.new('ShaderNodeUVMap')
        nt.links.new(uvn.outputs['UV'], mapa.inputs['Vector'])
    return mapa.outputs['Vector']


def vidro_passa_luz(m):
    """O raio de sombra atravessa o vidro: o sol e o céu entram no ambiente sem virar ruído (truque padrão de interiores)."""
    nt = m.node_tree
    saida = next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
    if not saida or not saida.inputs['Surface'].is_linked:
        return
    origem = saida.inputs['Surface'].links[0].from_socket
    lp = nt.nodes.new('ShaderNodeLightPath')
    tr = nt.nodes.new('ShaderNodeBsdfTransparent')
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(lp.outputs['Is Shadow Ray'], mix.inputs['Fac'])
    nt.links.new(origem, mix.inputs[1])
    nt.links.new(tr.outputs['BSDF'], mix.inputs[2])
    nt.links.new(mix.outputs['Shader'], saida.inputs['Surface'])


contagem, trocadas = {}, []
for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    nt = m.node_tree
    pr = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if not pr:
        continue
    cl = classe_de(m.name)
    contagem[cl] = contagem.get(cl, 0) + 1
    p = ACABAMENTO.get(cl, ACABAMENTO['outro'])
    tex = pr.inputs['Base Color'].links[0].from_node if pr.inputs['Base Color'].is_linked else None
    tex = tex if tex is not None and tex.type == 'TEX_IMAGE' and tex.image else None
    if not pr.inputs['Roughness'].is_linked:
        pr.inputs['Roughness'].default_value = p['rug']
    pr.inputs['Metallic'].default_value = 1.0 if cl in ('metal', 'espelho') else 0.0
    if cl == 'vidro':
        pr.inputs['Transmission Weight'].default_value = 1.0
        pr.inputs['IOR'].default_value = 1.5
        if not pr.inputs['Base Color'].is_linked:
            pr.inputs['Base Color'].default_value = (0.97, 0.98, 0.98, 1)
        vidro_passa_luz(m)
        continue
    if cl == 'espelho' and not pr.inputs['Base Color'].is_linked:
        pr.inputs['Base Color'].default_value = (0.9, 0.9, 0.9, 1)
    if cl == 'espelho' and tex:  # o "reflexo pintado" do SketchUp sai; o espelho reflete de verdade
        nt.links.remove(pr.inputs['Base Color'].links[0])
        pr.inputs['Base Color'].default_value = (0.9, 0.9, 0.9, 1)
    if p.get('brilho_tecido'):
        pr.inputs['Sheen Weight'].default_value = p['brilho_tecido']
        pr.inputs['Sheen Roughness'].default_value = 0.4
    if p.get('translucencia'):
        pr.inputs['Subsurface Weight'].default_value = p['translucencia']
    if p.get('verniz'):  # madeira com verniz acetinado: um brilho fino por cima do veio
        pr.inputs['Coat Weight'].default_value = p['verniz']
        pr.inputs['Coat Roughness'].default_value = 0.3

    fina = FINAS.get(cl) if cfg.get('texturas_finas', True) else None
    if fina and tex:
        orig_m = metros_por_repeticao(m) or fina['metros']
        vetor = coordenada(nt, tex, orig_m / fina['metros'])
        grande = min(tex.image.size) >= 1500  # textura do arquiteto já é fina: a cor dele fica
        if fina['trocar'] and not grande:
            diff = nt.nodes.new('ShaderNodeTexImage')
            diff.image = imagem(fina['base'], 'diff', True)
            nt.links.new(vetor, diff.inputs['Vector'])
            alvo, base = media_linear(tex.image), media_linear(diff.image)
            if cfg.get('madeira_tom', 'natural') == 'original':  # o tom exato da textura do SketchUp
                razao = np.clip(alvo / np.maximum(base, 1e-4), 0.2, 4.0)
            else:  # o tom da madeira real, no claro/escuro que o arquiteto escolheu
                lum = np.array([0.2126, 0.7152, 0.0722], np.float32)
                razao = np.full(3, np.clip(float(alvo @ lum) / max(float(base @ lum), 1e-4), 0.2, 4.0))
            mult = nt.nodes.new('ShaderNodeMix')
            mult.data_type = 'RGBA'
            mult.blend_type = 'MULTIPLY'
            mult.inputs['Factor'].default_value = 1.0
            nt.links.new(diff.outputs['Color'], mult.inputs[6])
            mult.inputs[7].default_value = (*[float(x) for x in razao], 1)
            nt.links.new(mult.outputs[2], pr.inputs['Base Color'])
            trocadas.append(m.name)
        nor = nt.nodes.new('ShaderNodeTexImage')
        nor.image = imagem(fina['base'], 'nor', False)
        nt.links.new(vetor, nor.inputs['Vector'])
        nm = nt.nodes.new('ShaderNodeNormalMap')
        nm.inputs['Strength'].default_value = fina['normal']
        nt.links.new(nor.outputs['Color'], nm.inputs['Color'])
        nt.links.new(nm.outputs['Normal'], pr.inputs['Normal'])
        rg = nt.nodes.new('ShaderNodeTexImage')
        rg.image = imagem(fina['base'], 'rough', False)
        nt.links.new(vetor, rg.inputs['Vector'])
        faixa = nt.nodes.new('ShaderNodeMapRange')
        faixa.inputs['To Min'].default_value = max(p['rug'] - 0.12, 0.05)
        faixa.inputs['To Max'].default_value = min(p['rug'] + 0.12, 1.0)
        nt.links.new(rg.outputs['Color'], faixa.inputs['Value'])
        nt.links.new(faixa.outputs['Result'], pr.inputs['Roughness'])
    elif tex and p.get('relevo') and not pr.inputs['Normal'].is_linked:
        # relevo tirado da própria textura: só onde o acabamento tem relevo de verdade (rejunte, grafiato)
        bump = nt.nodes.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value = p['relevo']
        bump.inputs['Distance'].default_value = 0.001
        nt.links.new(tex.outputs['Color'], bump.inputs['Height'])
        nt.links.new(bump.outputs['Normal'], pr.inputs['Normal'])

# 2b. Piso: o material que cobre o chão (faces horizontais na cota mais baixa) vira porcelanato
# acetinado com reflexo, salvo se for madeira, tapete ou tecido. O SketchUp não diz o que é piso.
areas_piso, cotas = {}, []
for o in malhas:
    if o.hide_render:
        continue
    mw = o.matrix_world
    n3 = mw.to_3x3().inverted_safe().transposed()
    esc = abs(mw.determinant()) ** (2 / 3)
    for pg in o.data.polygons:
        if pg.area * esc < 0.02:
            continue
        if (n3 @ pg.normal).normalized().z > 0.97:
            cotas.append(((mw @ pg.center).z, pg.area * esc, o, pg.material_index))
if cotas:
    cotas.sort(key=lambda t: t[0])
    total = sum(a for _, a, *_ in cotas)
    acum, z_chao = 0, cotas[0][0]
    for z, a, *_ in cotas:  # cota em que já está 15% da área horizontal: o chão
        acum += a
        if acum > total * 0.15:
            z_chao = z
            break
    for z, a, o, mi in cotas:
        if abs(z - z_chao) < 0.06 and mi < len(o.material_slots) and o.material_slots[mi].material:
            nome = o.material_slots[mi].material.name
            areas_piso[nome] = areas_piso.get(nome, 0) + a
pisos_polidos = []
for nome, a in areas_piso.items():
    m = bpy.data.materials.get(nome)
    if a < 3 or not m or not m.use_nodes or classe_de(nome) in ('madeira', 'piso_madeira', 'tapete', 'tecido', 'boucle', 'suede'):
        continue
    pr = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if pr and not pr.inputs['Roughness'].is_linked and pr.inputs['Metallic'].default_value < 0.5:
        pr.inputs['Roughness'].default_value = cfg.get('piso_rugosidade', 0.1)
        pisos_polidos.append(nome)

# Ninguém tem superfície perfeitamente uniforme: uma variação leve na rugosidade tira o "ar de CG".
for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    nt = m.node_tree
    pr = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if not pr or pr.inputs['Roughness'].is_linked or pr.inputs['Transmission Weight'].default_value > 0.5:
        continue
    r = pr.inputs['Roughness'].default_value
    coord = nt.nodes.new('ShaderNodeTexCoord')
    ruido = nt.nodes.new('ShaderNodeTexNoise')
    ruido.inputs['Scale'].default_value = 1.5
    ruido.inputs['Detail'].default_value = 6
    faixa = nt.nodes.new('ShaderNodeMapRange')
    faixa.inputs['From Min'].default_value = 0.3
    faixa.inputs['From Max'].default_value = 0.7
    faixa.inputs['To Min'].default_value = max(r * 0.8, 0.02)
    faixa.inputs['To Max'].default_value = min(r * 1.2 + 0.02, 1.0)
    nt.links.new(coord.outputs['Object'], ruido.inputs['Vector'])
    nt.links.new(ruido.outputs['Fac'], faixa.inputs['Value'])
    nt.links.new(faixa.outputs['Result'], pr.inputs['Roughness'])

# ---------------------------------------------------------------- 3. luz
# Presets de fotógrafo; "sol" na cena (elevação/azimute/força) ainda vale por cima do preset.
LUZES = {
    'dia': dict(elevacao=35, azimute=330, forca=3.5, cor=(1.0, 0.95, 0.88), ceu=1.3, rebatedor=80, spots=35),
    'difusa': dict(elevacao=60, azimute=330, forca=0.0, cor=(1.0, 1.0, 1.0), ceu=2.2, rebatedor=110, spots=35),
    'tarde': dict(elevacao=12, azimute=300, forca=4.0, cor=(1.0, 0.8, 0.6), ceu=0.8, rebatedor=60, spots=70),
}
luz = dict(LUZES.get(cfg.get('luz', 'dia'), LUZES['dia']))
luz.update({k: v for k, v in cfg.get('sol', {}).items() if k in ('elevacao', 'azimute', 'forca')})
if 'ceu_forca' in cfg:
    luz['ceu'] = cfg['ceu_forca']
if 'rebatedor_watts' in cfg:
    luz['rebatedor'] = cfg['rebatedor_watts']
if 'spot_watts' in cfg:
    luz['spots'] = cfg['spot_watts']

w = bpy.data.worlds.new('ceu') if not sc.world else sc.world
sc.world = w
w.use_nodes = True
wn = w.node_tree
wn.nodes.clear()
sky = wn.nodes.new('ShaderNodeTexSky')
sky.sky_type = 'NISHITA' if 'NISHITA' in [i.identifier for i in sky.bl_rna.properties['sky_type'].enum_items] else sky.sky_type
sky.sun_elevation = math.radians(luz['elevacao'])
if hasattr(sky, 'sun_disc'):
    sky.sun_disc = False  # o sol de verdade é a lâmpada abaixo; o céu só dá a luz ambiente
bg = wn.nodes.new('ShaderNodeBackground')
bg.inputs['Strength'].default_value = luz['ceu']
out = wn.nodes.new('ShaderNodeOutputWorld')
wn.links.new(sky.outputs['Color'], bg.inputs['Color'])
if cfg.get('ceu_foto'):
    # Céu fotográfico (HDRI CC0) só no que a câmera vê pela janela e nos reflexos;
    # a luz que entra no ambiente continua vindo do céu físico + sol, controláveis por hora.
    env = wn.nodes.new('ShaderNodeTexEnvironment')
    env.image = bpy.data.images.load(cfg['ceu_foto'])
    mapa = wn.nodes.new('ShaderNodeMapping')
    mapa.inputs['Rotation'].default_value[2] = math.radians(cfg.get('ceu_foto_rotacao', 0))
    coord = wn.nodes.new('ShaderNodeTexCoord')
    wn.links.new(coord.outputs['Generated'], mapa.inputs['Vector'])
    wn.links.new(mapa.outputs['Vector'], env.inputs['Vector'])
    bg_foto = wn.nodes.new('ShaderNodeBackground')
    bg_foto.inputs['Strength'].default_value = cfg.get('ceu_foto_forca', 0.8) * max(luz['ceu'], 0.5)
    wn.links.new(env.outputs['Color'], bg_foto.inputs['Color'])
    caminho = wn.nodes.new('ShaderNodeLightPath')
    soma = wn.nodes.new('ShaderNodeMath')
    soma.operation = 'MAXIMUM'
    wn.links.new(caminho.outputs['Is Camera Ray'], soma.inputs[0])
    wn.links.new(caminho.outputs['Is Glossy Ray'], soma.inputs[1])
    mix = wn.nodes.new('ShaderNodeMixShader')
    wn.links.new(soma.outputs['Value'], mix.inputs['Fac'])
    wn.links.new(bg.outputs['Background'], mix.inputs[1])
    wn.links.new(bg_foto.outputs['Background'], mix.inputs[2])
    wn.links.new(mix.outputs['Shader'], out.inputs['Surface'])
else:
    wn.links.new(bg.outputs['Background'], out.inputs['Surface'])

if luz['forca'] > 0:
    # azimute em graus no plano do modelo: de onde o sol vem (0 = +Y, 90 = +X)
    sol = bpy.data.objects.new('sol', bpy.data.lights.new('sol', 'SUN'))
    sc.collection.objects.link(sol)
    sol.data.energy = luz['forca']
    sol.data.color = luz['cor']
    sol.data.angle = math.radians(1.5)  # sombra com borda macia, como sol filtrado
    el, az = math.radians(luz['elevacao']), math.radians(luz['azimute'])
    para_o_sol = Vector((math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)))
    sol.rotation_euler = para_o_sol.to_track_quat('Z', 'Y').to_euler()

# Luz rebatida: um painel grande e invisível no teto, como o rebatedor de um fotógrafo de interiores.
# Não aparece na imagem nem nos reflexos; só tira o apartamento da penumbra.
if luz['rebatedor'] > 0:
    vis = [o for o in malhas if not o.hide_render]
    pts = [o.matrix_world @ Vector(v) for o in vis for v in (o.bound_box[0], o.bound_box[6])]
    bx0, bx1 = min(p.x for p in pts), max(p.x for p in pts)
    by0, by1 = min(p.y for p in pts), max(p.y for p in pts)
    reb = bpy.data.objects.new('rebatedor', bpy.data.lights.new('rebatedor', 'AREA'))
    sc.collection.objects.link(reb)
    reb.data.shape = 'RECTANGLE'
    reb.data.size, reb.data.size_y = bx1 - bx0, by1 - by0
    reb.data.energy = luz['rebatedor']
    reb.data.color = (1.0, 0.94, 0.86)
    reb.location = ((bx0 + bx1) / 2, (by0 + by1) / 2, cfg.get('rebatedor_altura', 2.3))
    reb.visible_camera = False
    reb.visible_glossy = False
    reb.visible_transmission = False

# Luminárias do modelo: cada spot embutido ganha uma luz quente apontada para baixo,
# e a face de baixo do corpo da luminária acende (o pontinho de luz no forro, como na foto).
spots = 0
corpo = re.compile(r'corpo_luz', re.I)
if cfg.get('acender_spots', True) and luz['spots'] > 0:
    for m in bpy.data.materials:
        if not (m.use_nodes and corpo.search(m.name)):
            continue
        pr = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if not pr:
            continue
        nt = m.node_tree
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        sep = nt.nodes.new('ShaderNodeSeparateXYZ')
        menor = nt.nodes.new('ShaderNodeMath')
        menor.operation = 'LESS_THAN'
        menor.inputs[1].default_value = -0.7
        forca = nt.nodes.new('ShaderNodeMath')
        forca.operation = 'MULTIPLY'
        forca.inputs[1].default_value = cfg.get('spot_brilho', 25.0)
        nt.links.new(geo.outputs['Normal'], sep.inputs['Vector'])
        nt.links.new(sep.outputs['Z'], menor.inputs[0])
        nt.links.new(menor.outputs['Value'], forca.inputs[0])
        pr.inputs['Emission Color'].default_value = (1.0, 0.82, 0.62, 1)
        nt.links.new(forca.outputs['Value'], pr.inputs['Emission Strength'])
    for o in malhas:
        if o.hide_render or not any(s and corpo.search(s.name) for s in o.data.materials):
            continue
        cantos = [o.matrix_world @ Vector(v) for v in o.bound_box]
        base = sum(cantos, Vector()) / 8
        base.z = min(v.z for v in cantos) - 0.02
        l = bpy.data.objects.new('spot', bpy.data.lights.new('spot', 'SPOT'))
        sc.collection.objects.link(l)
        l.location = base
        l.data.energy = luz['spots']
        l.data.color = (1.0, 0.8, 0.6)  # cerca de 3000 K
        l.data.spot_size = math.radians(90)
        l.data.spot_blend = 0.6
        l.data.shadow_soft_size = 0.03
        spots += 1
for o in malhas:  # feixes de luz falsos (geometria) não entram no render físico
    if any(s and re.search(r'light beam', s.name, re.I) for s in o.data.materials):
        o.hide_render = True

# ---------------------------------------------------------------- 4. câmera
dg = bpy.context.evaluated_depsgraph_get()
def piso(x, y):
    # desce do meio do pé-direito juntando todas as superfícies; o piso é a mais alta
    # entre as que ficam até 40 cm acima da mais baixa (ignora sofá, mesa, tapete alto)
    zs, z = [], 1.5
    while True:
        ok, loc, *_ = sc.ray_cast(dg, Vector((x, y, z)), Vector((0, 0, -1)))
        if not ok:
            break
        zs.append(loc.z)
        z = loc.z - 0.001
    if not zs:
        return 0.0
    return max(v for v in zs if v < min(zs) + 0.4)
c = cfg['camera']
z0 = piso(c['x'], c['y'])
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
sc.collection.objects.link(cam)
# "z"/"alvo_z" absolutos vêm do visualizador 3D (mesma geometria); senão, altura acima do piso
cam.location = (c['x'], c['y'], c['z'] if 'z' in c else z0 + c.get('altura', 1.3))
alvo = Vector((c['alvo_x'], c['alvo_y'], c['alvo_z'] if 'alvo_z' in c else z0 + c.get('alvo_altura', 1.1)))
cam.data.lens = c.get('lente', 20)
cam.data.sensor_width = 36
cam.data.sensor_fit = 'HORIZONTAL'
if c.get('paredes_retas', True):
    # como a lente de fotógrafo de arquitetura: câmera nivelada (verticais ficam verticais)
    # e o quadro desliza para cima/baixo até o alvo
    plano = Vector((alvo.x - cam.location.x, alvo.y - cam.location.y, 0))
    cam.rotation_euler = plano.to_track_quat('-Z', 'Y').to_euler()
    dz = alvo.z - cam.location.z
    cam.data.shift_y = c.get('shift_y', (dz / max(plano.length, 0.1)) * cam.data.lens / cam.data.sensor_width)
else:
    cam.rotation_euler = (alvo - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.shift_y = c.get('shift_y', 0.0)
# "recorte": a câmera pode ficar dentro/atrás de uma parede e ignorar o que está a menos de X metros
cam.data.clip_start = max(0.01, float(c.get('recorte', 0.05)))
sc.camera = cam

# ---------------------------------------------------------------- 5. Cycles
sc.render.engine = 'CYCLES'
cy = sc.cycles
prefs = bpy.context.preferences.addons['cycles'].preferences
for tipo in ('OPTIX', 'CUDA', 'METAL'):
    try:
        prefs.compute_device_type = tipo
        prefs.get_devices()
        if any(d.type == tipo for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == tipo
            cy.device = 'GPU'
            break
    except TypeError:
        continue
sc.render.use_persistent_data = True  # a prévia do fotômetro e o render final dividem a mesma cena montada
cy.use_adaptive_sampling = True
cy.use_denoising = True
cy.denoiser = 'OPENIMAGEDENOISE'
cy.denoising_input_passes = 'RGB_ALBEDO_NORMAL'
cy.denoising_prefilter = 'ACCURATE'
cy.max_bounces = 16
cy.diffuse_bounces = 10
cy.glossy_bounces = 8
cy.transmission_bounces = 16
cy.transparent_max_bounces = 16
cy.sample_clamp_indirect = 10
cy.blur_glossy = 0.5
# Khronos PBR Neutral mantém a cor que o arquiteto escolheu no material (o AgX lava os tons)
# (a lista de opções desse campo é dinâmica: só se descobre se o nome vale tentando)
for vt in (cfg.get('cor', 'Khronos PBR Neutral'), 'Khronos PBR Neutral', 'AgX'):
    try:
        sc.view_settings.view_transform = vt
        break
    except TypeError:
        continue
sc.view_settings.look = 'None'
W, H = cfg['resolucao']

# 5b. Fotômetro: uma prévia pequena, linear, sem pós. Dela saem a exposição (o ambiente claro, sem
# estourar a janela) e o balanço de branco (as paredes brancas saem brancas, não azuladas).
expo = cfg.get('exposicao')
ganhos = (1.0, 1.0, 1.0)
medida = {}
medida_balanco = {}
if expo is None or cfg.get('balanco_auto', 0.4) > 0:
    r = sc.render
    r.resolution_x, r.resolution_y = 480, max(16, round(480 * H / W))
    r.resolution_percentage = 100
    cy.samples = 32
    cy.adaptive_threshold = 0.05
    r.use_compositing = False
    sc.view_settings.exposure = 0
    r.image_settings.file_format = 'OPEN_EXR'
    r.image_settings.color_depth = '32'
    previa = os.path.join(tempfile.mkdtemp(), 'previa.exr')
    r.filepath = previa
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(previa)
    px = np.empty(im.size[0] * im.size[1] * 4, np.float32)
    im.pixels.foreach_get(px)
    rgb = px.reshape(-1, 4)[:, :3]
    Y = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    ok = Y > 1e-5
    rgb, Y = rgb[ok], Y[ok]
    p97 = float(np.percentile(Y, 97))
    dentro = Y < p97  # janelas e lâmpadas fora da conta
    chave = float(np.exp(np.mean(np.log(Y[dentro] + 1e-4))))
    if expo is None:
        # meio-tom do ambiente em ~0,32 (linear) sem passar 0,95 nos 3% mais claros
        expo = min(math.log2(cfg.get('chave_alvo', 0.32) / chave), math.log2(0.95 / max(p97, 1e-4)) + 0.6)
        expo = max(-4.0, min(6.0, expo)) + cfg.get('exposicao_ajuste', 0.0)
    if cfg.get('balanco_auto', 0.4) > 0:
        croma = (rgb.max(1) - rgb.min(1)) / np.maximum(Y, 1e-4)
        p30 = np.percentile(Y, 30)
        neutros = (croma < 0.25) & (Y > p30) & dentro
        if neutros.sum() > 200:
            mr, mg, mb = rgb[neutros].mean(0)
            # dominante = quanto o "branco" está quente (ambiente fechado, só com spots de 3000 K) ou frio;
            # dominante leve → correção leve (mantém o clima); dominante forte → corrige quase tudo
            dominante = float(max(mr, mb) / max(min(mr, mb), 1e-5))
            k = cfg.get('balanco_auto', 0.4)
            k = float(np.clip(k + max(0.0, dominante - 1.12) * 2.5, k, 0.9))
            ganhos = tuple(float(np.clip((mg / max(v, 1e-5)) ** k, 0.7, 1.35)) for v in (mr, mg, mb))
            medida_balanco = dict(dominante=round(dominante, 3), forca=round(k, 2))
    medida = dict(chave=round(chave, 4), p97=round(p97, 4), **medida_balanco)
    bpy.data.images.remove(im)
# Um toque de calor, como a foto de interiores de lançamento (a luz do céu deixa tudo levemente azulado)
calor = cfg.get('calor', 0.03) if medida_balanco.get('dominante', 1.0) < 1.12 else 0.0
ganhos = (ganhos[0] * (1 + calor), ganhos[1], ganhos[2] * (1 - calor * 1.3))
cy.samples = cfg.get('amostras', 128)
cy.adaptive_threshold = cfg.get('limiar_ruido', 0.01)
sc.view_settings.exposure = expo
sc.view_settings.use_curve_mapping = False

pos = cfg.get('pos', {})
if pos.get('contraste', 0.16) > 0:
    # curva em S suave, no espaço da tela (depois do mapeamento de cor): sombras mais fundas, altas preservadas
    k = pos.get('contraste', 0.16)
    sc.view_settings.use_curve_mapping = True
    curva = sc.view_settings.curve_mapping.curves[3]
    curva.points[0].location = (0, 0)
    curva.points[1].location = (1, 1)
    curva.points.new(0.15, 0.15 - k * 0.25)  # só as sombras fundas descem; o meio-tom fica claro
    curva.points.new(0.7, min(0.7 + k * 0.2, 1))
    sc.view_settings.curve_mapping.update()
# Pós-produção do fotógrafo: balanço de branco, brilho nas luzes fortes, saturação, vinheta leve, nitidez
ng = bpy.data.node_groups.new('pos', 'CompositorNodeTree')
ng.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
N, L = ng.nodes, ng.links
rl = N.new('CompositorNodeRLayers')
ultimo = rl.outputs['Image']
if ganhos != (1.0, 1.0, 1.0):
    wb = N.new('ShaderNodeMix')
    wb.data_type = 'RGBA'
    wb.blend_type = 'MULTIPLY'
    wb.inputs['Factor'].default_value = 1.0
    L.new(ultimo, wb.inputs[6])
    wb.inputs[7].default_value = (*ganhos, 1)
    ultimo = wb.outputs[2]
if pos.get('ligado', True):
    glare = N.new('CompositorNodeGlare')
    glare.inputs['Type'].default_value = 'Bloom'
    glare.inputs['Threshold'].default_value = pos.get('brilho_limiar', 1.5)
    glare.inputs['Strength'].default_value = pos.get('brilho', 0.2)
    glare.inputs['Size'].default_value = pos.get('brilho_tamanho', 0.6)
    L.new(ultimo, glare.inputs['Image'])
    ultimo = glare.outputs['Image']
    if pos.get('temperatura'):
        cb = N.new('CompositorNodeColorBalance')
        cb.inputs['Type'].default_value = 'White Point'
        cb.inputs['Temperature'].default_value = 6500 - pos['temperatura']  # entrada mais fria = saída mais quente
        L.new(ultimo, cb.inputs['Image']); ultimo = cb.outputs['Image']
    if pos.get('saturacao', 1.08) != 1:
        hs = N.new('CompositorNodeHueSat')
        hs.inputs['Saturation'].default_value = pos.get('saturacao', 1.08)
        L.new(ultimo, hs.inputs['Image']); ultimo = hs.outputs['Image']
    if pos.get('vinheta', 0.12) > 0:
        el = N.new('CompositorNodeEllipseMask')
        el.inputs['Size'].default_value = (1.15, 1.15)
        bl = N.new('CompositorNodeBlur')
        bl.inputs['Size'].default_value = (W * 0.25, W * 0.25)
        L.new(el.outputs['Mask'], bl.inputs['Image'])
        mx = N.new('ShaderNodeMix')
        mx.data_type = 'RGBA'
        mx.blend_type = 'MULTIPLY'
        mx.inputs['Factor'].default_value = pos.get('vinheta', 0.12)
        L.new(ultimo, mx.inputs[6]); L.new(bl.outputs['Image'], mx.inputs[7])
        ultimo = mx.outputs[2]
    if pos.get('nitidez', 0.08) > 0:
        fl = N.new('CompositorNodeFilter')
        fl.inputs['Type'].default_value = 'Diamond Sharpen'
        fl.inputs['Factor'].default_value = pos.get('nitidez', 0.08)
        L.new(ultimo, fl.inputs['Image']); ultimo = fl.outputs['Image']
saida = N.new('NodeGroupOutput')
L.new(ultimo, saida.inputs[0])
sc.compositing_node_group = ng
sc.render.use_compositing = True
sc.render.resolution_x, sc.render.resolution_y = W, H
sc.render.resolution_percentage = 100
sc.render.image_settings.file_format = cfg.get('formato', 'PNG')
sc.render.image_settings.color_depth = '8'
if sc.render.image_settings.file_format == 'JPEG':
    sc.render.image_settings.quality = 95
sc.render.filepath = cfg['saida']

t1 = time.time()
bpy.ops.render.render(write_still=True)
print('RESUMO', json.dumps({'materiais': contagem, 'texturas_trocadas': trocadas, 'espelhadas': espelhadas,
                            'pisos_polidos': pisos_polidos, 'spots': spots, 'piso_z': round(z0, 3),
                            'cor': sc.view_settings.view_transform, 'exposicao': round(expo, 2),
                            'balanco': [round(g, 3) for g in ganhos], 'fotometro': medida, 'luz': cfg.get('luz', 'dia'),
                            'device': cy.device + ':' + prefs.compute_device_type,
                            'preparo_s': round(t1 - t0, 1), 'render_s': round(time.time() - t1, 1)}))
