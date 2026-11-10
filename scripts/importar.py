# Converte o .glb exportado do SketchUp em .blend (feito uma vez por modelo; o .blend fica em cache)
# blender -b --factory-startup -P importar.py -- modelo.glb saida.blend
import bpy, sys, time, json

src, out = sys.argv[sys.argv.index('--') + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
t = time.time()
bpy.ops.import_scene.gltf(filepath=src)
malhas = [o for o in bpy.data.objects if o.type == 'MESH']
bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
print('IMPORTADO', json.dumps({'segundos': round(time.time() - t, 1), 'malhas': len(malhas),
                               'materiais': len(bpy.data.materials)}))
