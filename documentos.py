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
                # Converte para string para garantir que não haja erro de tipo
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
    MOTOR 2 (RAIO-X DE XML): Usado APENAS para Projetos de Extensão.
    Varre o código fonte do Word para encontrar textos dentro de CAIXAS DE TEXTO flutuantes, 
    formas, cabeçalhos, rodapés e tabelas aninhadas, preservando o layout da faculdade.
    """
    doc = Document(arquivo_template)

    def substituir_em_paragrafo(p):
        if not p.text: return
        
        # Limpeza rápida de segurança caso haja espaços acidentais nas chaves (Ex: {{DATA_ 1}})
        texto_limpo = re.sub(r'\{\{\s*(.*?)\s*\}\}', lambda m: '{{' + m.group(1).replace(' ', '') + '}}', p.text)
        if p.text != texto_limpo:
            p.text = texto_limpo

        for marcador, valor in dicionario_dados.items():
            if marcador in p.text:
                # 1ª Tentativa: Substituir mantendo a formatação exata da fonte
                substituiu_run = False
                for run in p.runs:
                    if marcador in run.text:
                        run.text = run.text.replace(marcador, str(valor))
                        substituiu_run = True
                
                # 2ª Tentativa (Fallback): Se as tags estiverem separadas invisivelmente pelo Word
                # Substitui o texto bruto do parágrafo inteiro (Ideal para caixas de texto)
                if not substituiu_run:
                    p.text = p.text.replace(marcador, str(valor))

    # 1. VARREDURA PROFUNDA NO CORPO (Acha Caixas de Texto!)
    # Ao iterar sem argumentos, ele acha TUDO. Depois verificamos se é um parágrafo (CT_P)
    for node in doc.element.body.iter():
        if isinstance(node, CT_P):
            p = Paragraph(node, doc)
            substituir_em_paragrafo(p)

    # 2. VARREDURA NOS CABEÇALHOS E RODAPÉS
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
