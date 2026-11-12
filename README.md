# grp-render-worker

Trabalhador de render (RunPod Serverless) do Estúdio GRP Arquitetura: Blender 5.3 sem tela,
Cycles na GPU, materiais casados pelo nome, céu físico + sol, spots do modelo acesos,
piso com reflexo, câmera de arquitetura (verticais retas) e pós-produção (brilho, vinheta, contraste).

A imagem só contém o Blender, dois fundos e 15 texturas CC0 da Poly Haven e estes scripts. O modelo 3D chega
por um link assinado a cada job e fica em cache no volume do worker.

- `handler.py`: recebe `modelo_url`, `cena` e `formato`; devolve a imagem (base64 ou PUT em `upload_url`).
- `scripts/enxugar.py`: roda antes da importação (só Python, sem dependências). Junta a geometria idêntica byte a byte e
  sobe para a raiz da cena as peças sem cisalhamento; as deformadas levam uma cópia da própria cadeia de grupos, na
  mesma ordem. Num anteprojeto de 635 MB (198 mil nós, 26 mil malhas): importação de ~55 min → 20 s; num decorado
  as 6.382 peças e a ficha dos 436 materiais saem idênticas às do arquivo original. Se falhar, segue com o original.
- `scripts/importar.py`: .glb do SketchUp → .blend.
- `scripts/render_cena.py`: a vista em si (câmera, luz, materiais, Cycles). v4: materiais por classe
  (madeira, tecido, bouclé, pedra…), texturas finas CC0 da Poly Haven (`texturas.txt`) no lugar das texturas
  pequenas do SketchUp, vidro que deixa a luz passar, fotômetro (exposição e balanço de branco medidos
  numa prévia), presets de luz (`luz`: dia, difusa, tarde) e câmera em coordenadas absolutas do visualizador 3D.
- `scripts/previa.py`: versão leve do modelo (.glb com Draco, texturas WebP 512 px) para o visualizador 3D.
  Roda no próprio trabalhador com `acao: "versao_leve"` (o Estúdio pede sozinho quando um arquiteto envia um
  modelo à Biblioteca 3D) e devolve o .glb leve e a ficha dos materiais por PUT em links assinados.
- `scripts/limpeza.py`: descarta peças perdidas longe do modelo, com o mesmo critério no render e na versão leve
  (raio de 15 m, ou 2,5× a distância de 90% das peças em modelos maiores, como prédio inteiro ou área de lazer).
- O cache do .blend importado usa o `modelo_sha256` que o Estúdio manda (um nome reaproveitado nunca renderiza
  o modelo antigo); sem ele, a chave é o endereço do modelo.

A fonte fica no monorepo da GRP (`estudio-grp/render-worker`); este repositório é a cópia
que o GitHub Actions usa para construir `ghcr.io/rgvieira49/grp-render-worker`.
