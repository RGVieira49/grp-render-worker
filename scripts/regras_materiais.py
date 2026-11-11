# Regras de classe dos materiais (pelo nome), compartilhadas entre o render (render_cena.py) e a
# versão leve do visualizador (previa.py): o painel mostra a mesma classe que o render usa.
import re

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


def classe_pelo_nome(nome):
    n = nome.lower()
    for padrao, c in CLASSES:
        if re.search(padrao, n):
            return c
    return 'outro'
