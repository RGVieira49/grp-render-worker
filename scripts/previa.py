# Versão leve do modelo para o visualizador 3D do Estúdio (escolher a câmera no navegador).
# blender -b modelo.blend -P previa.py -- saida.glb [faces_por_objeto]
# Mesma limpeza do render (peças soltas fora), malhas pesadas simplificadas, texturas em no máx. 512 px,
# Draco. As coordenadas são as mesmas do render: o que se enquadra aqui é o que a GPU renderiza.
import bpy, sys, json, os, tempfile, time
from mathutils import Vector

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
pasta = tempfile.mkdtemp()
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
        pass

bpy.ops.export_scene.gltf(
    filepath=saida, export_format='GLB', export_apply=True, export_yup=True,
    export_image_format='WEBP', export_image_quality=70,  # com JPEG, texturas com alfa saem em PNG (pesado)
    export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
    export_draco_position_quantization=12, export_draco_normal_quantization=8, export_draco_texcoord_quantization=10,
    export_cameras=False, export_lights=False, export_animations=False,
)
print('PREVIA', json.dumps({'objetos': len(malhas), 'removidos': fora, 'simplificadas': simplificadas,
                            'segundos': round(time.time() - t0, 1)}))
