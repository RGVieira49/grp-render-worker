# Render de uma vista: blender -b modelo.blend -P render_cena.py -- cena.json
# A cena.json traz câmera, sol, resolução e amostras. O mesmo script roda no Mac (Metal) e na GPU alugada (OptiX).
import bpy, sys, json, math, re, time
from mathutils import Vector

cfg = json.load(open(sys.argv[sys.argv.index('--') + 1]))
sc = bpy.context.scene
t0 = time.time()

# 1. Limpeza: descarta peças soltas longe do apartamento
malhas = [o for o in sc.objects if o.type == 'MESH']
xs = sorted((o.matrix_world @ Vector(o.bound_box[0])).x for o in malhas)
ys = sorted((o.matrix_world @ Vector(o.bound_box[0])).y for o in malhas)
mx_, my_ = xs[len(xs) // 2], ys[len(ys) // 2]
for o in malhas:
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

# 2. Materiais: casamento pelo nome (o SketchUp exporta tudo com rugosidade 0,5 e sem metal)
REGRAS = [
    (r'vidro|glass|translucent|cristal', dict(transmissao=1.0, rug=0.02, ior=1.5)),
    (r'espelho|mirror', dict(metal=1.0, rug=0.02, cor=(0.9, 0.9, 0.9))),
    (r'a[çc]o|inox|cromad|chrome|metal|alum[ií]n|steel|lat[ãa]o|dourad|gold|bronze|cobre|niquel', dict(metal=1.0, rug=0.3)),
    (r'polid|polish', dict(rug=0.12)),
    (r'escovad|brushed', dict(rug=0.38)),
    (r'porcelanat|m[áa]rmore|marble|granit|quartz|travertin|silestone|dekton', dict(rug=0.14, relevo=0.05)),
    (r'cer[âa]mic|azulej|tile|pastilha|subway', dict(rug=0.22, relevo=0.08)),
    (r'madeira|wood|mdf|carvalho|freij[óo]|nogueira|louro|cumaru|ripad|lamin|amadeir', dict(rug=0.55, relevo=0.12)),
    (r'linho|tecido|fabric|veludo|velvet|boucl|algod|tapete|rug|almofad|cortina|curtain|manta|len[çc]ol|sof[áa]', dict(rug=0.9, brilho_tecido=0.4, relevo=0.2)),
    (r'couro|leather', dict(rug=0.45, relevo=0.1)),
    (r'cimento|concreto|concrete|reboco|textura', dict(rug=0.7, relevo=0.15)),
    (r'laca|laqu|lacad', dict(rug=0.25)),
    (r'pintura|tinta|suvinil|coral|parede|wall|gesso|forro', dict(rug=0.8)),
    (r'acr[ií]lic|pl[áa]stic', dict(rug=0.25)),
    (r'folha|leaf|planta|plant|vegeta', dict(rug=0.5, translucencia=0.3)),
]

def preset(nome):
    p = dict(rug=0.6)
    n = nome.lower()
    for padrao, valores in REGRAS:
        if re.search(padrao, n):
            p.update(valores)
    return p

contagem = {}
for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    nt = m.node_tree
    pr = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if not pr:
        continue
    p = preset(m.name)
    chave = 'vidro' if p.get('transmissao') else 'metal' if p.get('metal') else 'outros'
    contagem[chave] = contagem.get(chave, 0) + 1
    if not pr.inputs['Roughness'].is_linked:
        pr.inputs['Roughness'].default_value = p['rug']
    pr.inputs['Metallic'].default_value = p.get('metal', 0.0)
    if p.get('transmissao'):
        pr.inputs['Transmission Weight'].default_value = 1.0
        pr.inputs['IOR'].default_value = p['ior']
        pr.inputs['Base Color'].default_value = (1, 1, 1, 1) if not pr.inputs['Base Color'].is_linked else pr.inputs['Base Color'].default_value
    if 'cor' in p and not pr.inputs['Base Color'].is_linked:
        pr.inputs['Base Color'].default_value = (*p['cor'], 1)
    if p.get('brilho_tecido'):
        pr.inputs['Sheen Weight'].default_value = p['brilho_tecido']
    if p.get('translucencia'):
        pr.inputs['Subsurface Weight'].default_value = p['translucencia']
    # relevo a partir da própria textura de cor (sutil)
    tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE'), None)
    if tex and p.get('relevo') and not pr.inputs['Normal'].is_linked:
        bump = nt.nodes.new('ShaderNodeBump')
        bump.inputs['Strength'].default_value = p['relevo']
        bump.inputs['Distance'].default_value = 0.002
        nt.links.new(tex.outputs['Color'], bump.inputs['Height'])
        nt.links.new(bump.outputs['Normal'], pr.inputs['Normal'])

# 3. Céu e sol físicos
w = bpy.data.worlds.new('ceu') if not sc.world else sc.world
sc.world = w
w.use_nodes = True
wn = w.node_tree
wn.nodes.clear()
sky = wn.nodes.new('ShaderNodeTexSky')
sky.sky_type = 'NISHITA' if 'NISHITA' in [i.identifier for i in sky.bl_rna.properties['sky_type'].enum_items] else sky.sky_type
sky.sun_elevation = math.radians(cfg['sol']['elevacao'])
if hasattr(sky, 'sun_disc'):
    sky.sun_disc = False  # o sol de verdade é a lâmpada abaixo; o céu só dá a luz ambiente
bg = wn.nodes.new('ShaderNodeBackground')
bg.inputs['Strength'].default_value = cfg.get('ceu_forca', 0.6)
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
    bg_foto.inputs['Strength'].default_value = cfg.get('ceu_foto_forca', 0.8)
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

# azimute em graus no plano do modelo: de onde o sol vem (0 = +Y, 90 = +X)
sol = bpy.data.objects.new('sol', bpy.data.lights.new('sol', 'SUN'))
sc.collection.objects.link(sol)
sol.data.energy = cfg['sol'].get('forca', 6.0)
sol.data.color = (1.0, 0.93, 0.84)  # sol de fim de tarde, levemente quente
sol.data.angle = math.radians(0.6)

# Luz rebatida: um painel grande e invisível no teto, como o rebatedor de um fotógrafo de interiores.
# Não aparece na imagem nem nos reflexos; só tira o apartamento da penumbra.
if cfg.get('rebatedor_watts', 250) > 0:
    vis = [o for o in malhas if not o.hide_render]
    pts = [o.matrix_world @ Vector(v) for o in vis for v in (o.bound_box[0], o.bound_box[6])]
    bx0, bx1 = min(p.x for p in pts), max(p.x for p in pts)
    by0, by1 = min(p.y for p in pts), max(p.y for p in pts)
    reb = bpy.data.objects.new('rebatedor', bpy.data.lights.new('rebatedor', 'AREA'))
    sc.collection.objects.link(reb)
    reb.data.shape = 'RECTANGLE'
    reb.data.size, reb.data.size_y = bx1 - bx0, by1 - by0
    reb.data.energy = cfg.get('rebatedor_watts', 250)
    reb.data.color = (1.0, 0.9, 0.8)
    reb.location = ((bx0 + bx1) / 2, (by0 + by1) / 2, cfg.get('rebatedor_altura', 2.3))
    reb.visible_camera = False
    reb.visible_glossy = False
    reb.visible_transmission = False
el, az = math.radians(cfg['sol']['elevacao']), math.radians(cfg['sol']['azimute'])
para_o_sol = Vector((math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)))
sol.rotation_euler = para_o_sol.to_track_quat('Z', 'Y').to_euler()

# Luminárias do modelo: cada spot embutido ganha uma luz quente apontada para baixo
if cfg.get('acender_spots', True):
    corpo = re.compile(r'corpo_luz', re.I)
    spots = 0
    for o in malhas:
        if o.hide_render or not any(s and corpo.search(s.name) for s in o.data.materials):
            continue
        cantos = [o.matrix_world @ Vector(v) for v in o.bound_box]
        base = sum(cantos, Vector()) / 8
        base.z = min(v.z for v in cantos) - 0.02
        luz = bpy.data.objects.new('spot', bpy.data.lights.new('spot', 'SPOT'))
        sc.collection.objects.link(luz)
        luz.location = base
        luz.data.energy = cfg.get('spot_watts', 30)
        luz.data.color = (1.0, 0.78, 0.55)  # cerca de 3000 K
        luz.data.spot_size = math.radians(80)
        luz.data.spot_blend = 0.7
        luz.data.shadow_soft_size = 0.03
        spots += 1
for o in malhas:  # feixes de luz falsos (geometria) não entram no render físico
    if any(s and re.search(r'light beam', s.name, re.I) for s in o.data.materials):
        o.hide_render = True

# 4. Câmera: posição e alvo em planta; a altura sai do piso encontrado por raio
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
cam.location = (c['x'], c['y'], z0 + c.get('altura', 1.3))
alvo = Vector((c['alvo_x'], c['alvo_y'], z0 + c.get('alvo_altura', 1.1)))
cam.rotation_euler = (alvo - cam.location).to_track_quat('-Z', 'Y').to_euler()
cam.data.lens = c.get('lente', 20)
cam.data.sensor_width = 36
cam.data.shift_y = c.get('shift_y', 0.0)
cam.data.clip_start = 0.05
sc.camera = cam

# 5. Cycles
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
cy.samples = cfg.get('amostras', 128)
cy.use_adaptive_sampling = True
cy.adaptive_threshold = cfg.get('limiar_ruido', 0.01)
cy.use_denoising = True
cy.denoiser = 'OPENIMAGEDENOISE'
cy.denoising_input_passes = 'RGB_ALBEDO_NORMAL'
cy.denoising_prefilter = 'ACCURATE'
cy.max_bounces = 12
cy.diffuse_bounces = 8
cy.glossy_bounces = 6
cy.transmission_bounces = 12
cy.sample_clamp_indirect = 10
# Khronos PBR Neutral mantém a cor que o arquiteto escolheu no material (o AgX lava os tons)
transformes = [i.identifier for i in sc.view_settings.bl_rna.properties['view_transform'].enum_items]
sc.view_settings.view_transform = cfg.get('cor', 'Khronos PBR Neutral') if cfg.get('cor', 'Khronos PBR Neutral') in transformes else 'AgX'
sc.view_settings.look = 'None'
sc.view_settings.exposure = cfg.get('exposicao', 0.0)
sc.render.resolution_x, sc.render.resolution_y = cfg['resolucao']
sc.render.resolution_percentage = 100
sc.render.image_settings.file_format = cfg.get('formato', 'PNG')
if sc.render.image_settings.file_format == 'JPEG':
    sc.render.image_settings.quality = 92
sc.render.filepath = cfg['saida']

t1 = time.time()
bpy.ops.render.render(write_still=True)
print('RESUMO', json.dumps({'materiais': contagem, 'espelhadas': espelhadas, 'piso_z': round(z0, 3), 'device': cy.device + ':' + prefs.compute_device_type,
                            'preparo_s': round(t1 - t0, 1), 'render_s': round(time.time() - t1, 1)}))
