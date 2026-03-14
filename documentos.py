import io
import os
import re
from docx import Document
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph

def preencher_template_com_tags(arquivo_template, dicionario_dados):
    """
    MOTOR 1: Usado APENAS para os Trabalhos Acadêmicos gerados por IA.
    Aplica formatação Markdown (**negrito**) e regras específicas de títulos.
    """
    doc = Document(arquivo_template)
    
    def processar_paragrafo(paragrafo):
        texto_original = paragrafo.text
        tem_tag = False
        
        for marcador, texto_novo in dicionario_dados.items():
            if marcador in texto_original:
                texto_original = texto_original.replace(marcador, str(texto_novo))
                tem_tag = True
                
        if tem_tag:
            titulos_memorial = [
                "Resumo", "Contextualização do desafio", "Análise", 
                "Propostas de solução", "Conclusão reflexiva", 
                "Referências", "Autoavaliação"
            ]
            for t in titulos_memorial:
                if texto_original.strip().startswith(t): 
                    texto_original = texto_original.replace(t, f"**{t}**\n", 1)
                    
            titulos_aspectos = ["Aspecto 1:", "Aspecto 2:", "Aspecto 3:", "Por quê:"]
            for t in titulos_aspectos:
                if t in texto_original: 
                    texto_original = texto_original.replace(t, f"\n**{t}** " if "Por quê:" in t else f"**{t}** ")
                    
            if "?" in texto_original and "Por quê:" not in texto_original:
                partes = texto_original.split("?", 1)
                pergunta = partes[0].strip()
                if 10 < len(pergunta) < 150 and not pergunta.startswith("**"):
                    texto_original = f"**{pergunta}?**\n" + partes[1].lstrip()
                    
            texto_original = texto_original.replace("**\n ", "**\n").replace(":**\n:", ":**\n")
            paragrafo.clear()
            linhas = texto_original.split('\n')
            
            for i, linha in enumerate(linhas):
                partes = linha.split('**')
                for j, parte in enumerate(partes):
                    if parte: 
                        run = paragrafo.add_run(parte)
                        if j % 2 == 1: 
                            run.bold = True
                if i < len(linhas) - 1: 
                    paragrafo.add_run('\n')

    for paragrafo in doc.paragraphs: 
        processar_paragrafo(paragrafo)
        
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for paragrafo in celula.paragraphs: 
                    processar_paragrafo(paragrafo)

    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida


def preencher_template_extensao(arquivo_template, dicionario_dados):
    """
    MOTOR 2 (BLINDADO E À PROVA DE FALHAS): Usado APENAS para Projetos de Extensão.
    Faz auto-limpeza de erros de digitação nas tags e caça caixas de texto escondidas.
    """
    doc = Document(arquivo_template)

    def arrumar_tag_quebrada(match):
        # Limpa espaços, caracteres invisiveis e transforma em maiúsculo
        miolo = match.group(1).replace(' ', '').replace('\u200b', '').upper()
        miolo = miolo.replace('-', '_') # Se o usuário usou traço no lugar de underline, conserta
        
        # Se for uma tag de data com zero (Ex: DATA_08), remove o zero para casar com o sistema (DATA_8)
        if miolo.startswith('DATA_'):
            partes = miolo.split('_')
            if len(partes) == 2 and partes[1].isdigit():
                miolo = f"DATA_{int(partes[1])}"
                
        return f"{{{{{miolo}}}}}"

    def substituir_em_paragrafo(p):
        if not p.text: return
        
        texto_limpo = p.text
        # Remove lixo invisível do Word
        for char in ['\u200b', '\u200c', '\u200d', '\ufeff']:
            texto_limpo = texto_limpo.replace(char, '')
            
        # Aciona o "corretor automático" nas etiquetas do documento
        texto_limpo = re.sub(r'\{\{(.*?)\}\}', arrumar_tag_quebrada, texto_limpo)

        # Se o corretor mudou algo, aplica no documento real
        if p.text != texto_limpo:
            p.text = texto_limpo

        # Agora que tudo está limpo e padronizado, aplica os dados do cliente!
        for marcador, valor in dicionario_dados.items():
            if marcador in p.text:
                substituiu_run = False
                for run in p.runs:
                    run_limpo = run.text
                    for char in ['\u200b', '\u200c', '\u200d', '\ufeff']:
                        run_limpo = run_limpo.replace(char, '')
                        
                    if marcador in run_limpo:
                        run.text = run_limpo.replace(marcador, str(valor))
                        substituiu_run = True
                
                # Se falhar pelo run, aplica a força bruta no parágrafo todo
                if not substituiu_run:
                    p.text = p.text.replace(marcador, str(valor))

    # Varre todo o XML do documento (Pegando textos soltos e CAIXAS DE TEXTO)
    for node in doc.element.body.iter():
        if isinstance(node, CT_P):
            p = Paragraph(node, doc)
            substituir_em_paragrafo(p)

    # Varre Cabeçalhos e Rodapés
    for section in doc.sections:
        if section.header:
            for node in section.header._element.iter():
                if isinstance(node, CT_P):
                    p = Paragraph(node, section.header)
                    substituir_em_paragrafo(p)
        if section.footer:
            for node in section.footer._element.iter():
                if isinstance(node, CT_P):
                    p = Paragraph(node, section.footer)
                    substituir_em_paragrafo(p)

    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida


def extrair_texto_docx(arquivo_bytes):
    doc = Document(io.BytesIO(arquivo_bytes)) if isinstance(arquivo_bytes, bytes) else Document(arquivo_bytes)
    return "\n".join([p.text for p in doc.paragraphs])


def extrair_etapa_5(arquivo_bytes):
    doc = Document(io.BytesIO(arquivo_bytes))
    linhas = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    idx_inicio = -1
    
    for i, linha in enumerate(linhas):
        if "Lembre-se também de salvar este documento" in linha:
            idx_inicio = i + 1
            
    if idx_inicio == -1:
        for i in range(len(linhas)-1, -1, -1):
            if "memorial analítico" in linhas[i].lower() and "redação" not in linhas[i].lower():
                idx_inicio = i
                break
                
    if idx_inicio == -1 or idx_inicio >= len(linhas):
        return False, "Não foi possível separar as instruções do texto final. O arquivo pode estar fora do padrão."
        
    linhas_finais = linhas[idx_inicio:]
    headers_oficiais = [
        "Resumo", "Contextualização do desafio", "Análise", 
        "Propostas de solução", "Conclusão reflexiva", "Referências", "Autoavaliação"
    ]
    
    blocos = ["Memorial\nAnalítico"]
    
    for linha in linhas_finais:
        linha_limpa = linha.replace('**', '').strip()
        if not linha_limpa: continue
        
        if linha_limpa.lower() == "memorial analítico":
            continue
            
        is_header = False
        for h in headers_oficiais:
            if linha_limpa.startswith(h):
                blocos.append(h)
                resto = linha_limpa[len(h):].strip()
                if resto.startswith('-') or resto.startswith(':'):
                    resto = resto[1:].strip()
                if resto:
                    blocos.append(resto)
                is_header = True
                break
                
        if not is_header:
            blocos.append(linha_limpa)
            
    resultado = "\n\n".join(blocos)
    return True, resultado
