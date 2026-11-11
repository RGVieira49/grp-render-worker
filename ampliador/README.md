# grp-ampliador

Ampliador fotográfico (RunPod Serverless) do Estúdio GRP Arquitetura. É o passo depois do render físico
(`render-worker`): amplia (até 8K) e acrescenta micro-detalhe de foto sem mudar o projeto.

- SDXL fotorrealista (RealVisXL V5.0, openrail++) em img2img de baixa intensidade (`forca` 0,3),
  preso ao contorno do render pelo ControlNet Tile (xinsir, Apache-2.0), VAE fp16-fix (MIT).
- Ladrilhos de 1024 px com 256 px de sobreposição e emenda em rampa; mesma semente em todos.
- No fim, a luz e a cor "grandes" (frequências baixas) voltam a ser as do render: a IA só põe detalhe.
- Entrada: `imagem_url` e `upload_url` (links assinados do Estúdio), `escala`, `forca`, `detalhe`.
