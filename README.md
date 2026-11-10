# grp-render-worker

Trabalhador de render (RunPod Serverless) do Estúdio GRP Arquitetura: Blender 5.3 sem tela,
Cycles na GPU, materiais casados pelo nome, céu físico + sol, spots do modelo acesos,
piso com reflexo, câmera de arquitetura (verticais retas) e pós-produção (brilho, vinheta, contraste).

A imagem só contém o Blender, dois fundos CC0 da Poly Haven (céu e jardim) e estes scripts. O modelo 3D chega
por um link assinado a cada job e fica em cache no volume do worker.

- `handler.py`: recebe `modelo_url`, `cena` e `formato`; devolve a imagem (base64 ou PUT em `upload_url`).
- `scripts/importar.py`: .glb do SketchUp → .blend.
- `scripts/render_cena.py`: a vista em si (câmera, luz, materiais, Cycles).

A fonte fica no monorepo da GRP (`estudio-grp/render-worker`); este repositório é a cópia
que o GitHub Actions usa para construir `ghcr.io/rgvieira49/grp-render-worker`.
