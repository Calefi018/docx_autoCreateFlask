import os
import io
import json
from flask import Flask, render_template, request, send_file
from docx import Document
import google.generativeai as genai

app = Flask(__name__)

# Configura a chave logo na inicialização do servidor para conseguir listar os modelos
CHAVE_API = os.environ.get("GEMINI_API_KEY")
if CHAVE_API:
    genai.configure(api_key=CHAVE_API)

# =========================================================
# FUNÇÕES DA FERRAMENTA 1: PREENCHEDOR INTELIGENTE (INJETA)
# =========================================================
def preencher_template_inteligente(arquivo_template, respostas_json):
    doc = Document(arquivo_template)
    
    def inserir_resposta_abaixo(paragrafo_alvo, texto_resposta):
        novo_p = doc.add_paragraph()
        novo_p.style = doc.styles['Normal']
        partes = texto_resposta.split('**')
        for i, parte in enumerate(partes):
            if i % 2 == 1:
                novo_p.add_run(parte).bold = True
            else:
                novo_p.add_run(parte)
        paragrafo_alvo._p.addnext(novo_p._p)

    preenchidos = {"etapa_2": False, "etapa_3": False, "etapa_4": False, "etapa_5": False}
    
    for p in doc.paragraphs:
        texto_upper = p.text.upper()
        if "ETAPA 2:" in texto_upper and not preenchidos["etapa_2"]:
            inserir_resposta_abaixo(p, f"\n{respostas_json.get('etapa_2', '')}\n")
            preenchidos["etapa_2"] = True
        elif "ETAPA 3:" in texto_upper and not preenchidos["etapa_3"]:
            inserir_resposta_abaixo(p, f"\n{respostas_json.get('etapa_3', '')}\n")
            preenchidos["etapa_3"] = True
        elif "ETAPA 4:" in texto_upper and not preenchidos["etapa_4"]:
            inserir_resposta_abaixo(p, f"\n{respostas_json.get('etapa_4', '')}\n")
            preenchidos["etapa_4"] = True
        elif "ETAPA 5" in texto_upper and "AVALIATIVA" in texto_upper and not preenchidos["etapa_5"]:
            inserir_resposta_abaixo(p, f"\n{respostas_json.get('etapa_5', '')}\n")
            preenchidos["etapa_5"] = True

    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida

def gerar_respostas_ia_preenchedor(texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Você é um especialista acadêmico ajudando um estudante universitário.
    Vou te fornecer o TEMA/CASO de um Desafio Profissional.
    Sua tarefa é gerar as respostas originais e sem plágio para preencher as Etapas 2, 3, 4 e 5 do trabalho.
    
    REGRA OBRIGATÓRIA DA ETAPA 5 (MEMORIAL ANALÍTICO):
    Você deve redigir a Etapa 5 seguindo estritamente este padrão com os títulos em **negrito**:
    **Resumo:** [1 parágrafo]
    **Contextualização do desafio:** [Quem? Onde? Situação?]
    **Análise:** [Conceitos e exemplos]
    **Propostas de solução:** [Recomendações]
    **Conclusão reflexiva:** [Aprendizado]
    **Referências:** [ABNT]
    **Autoavaliação:** [Processo de estudo]
    
    FORMATO DE SAÍDA OBRIGATÓRIO:
    Retorne APENAS um objeto JSON válido. Não adicione crases (```json).
    As chaves do JSON devem ser EXATAMENTE estas:
    {{
        "etapa_2": "Escreva aqui os 3 aspectos mais relevantes e justifique...",
        "etapa_3": "Escreva aqui a lista comentada de conceitos teóricos...",
        "etapa_4": "Escreva aqui a aplicação dos conceitos e as soluções...",
        "etapa_5": "**Resumo:** ...\\n**Contextualização do desafio:** ...\\n**Análise:** ...\\n**Propostas de solução:** ...\\n**Conclusão reflexiva:** ...\\n**Referências:** ...\\n**Autoavaliação:** ..."
    }}
    DESCRIÇÃO DO TEMA/CASO DO DESAFIO:
    {texto_tema}
    """
    resposta = modelo.generate_content(prompt)
    texto_limpo = resposta.text.strip().replace("```json", "").replace("```", "")
    return json.loads(texto_limpo)

# =========================================================
# FUNÇÕES DA FERRAMENTA 2: GERADOR UNIVERSAL (GABARITO)
# =========================================================
def extrair_texto_docx(arquivo_upload):
    doc = Document(arquivo_upload)
    texto_completo = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(texto_completo)

def criar_gabarito_word(texto_ia):
    doc = Document()
    doc.add_heading('Gabarito Gerado - Desafio Profissional', level=1)
    
    linhas = texto_ia.split('\n')
    for linha in linhas:
        linha = linha.strip()
        if not linha: continue
            
        p = doc.add_paragraph()
        partes = linha.split('**')
        for i, parte in enumerate(partes):
            if i % 2 == 1:
                p.add_run(parte).bold = True
            else:
                p.add_run(parte)
                
    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida

def gerar_resolucao_inteligente_gabarito(texto_template, texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Você é um especialista acadêmico ajudando um estudante universitário a resolver um Desafio Profissional.
    
    1. A DESCRIÇÃO DO TEMA/CASO: {texto_tema}
    2. O TEXTO DO TEMPLATE PADRÃO ÚNICO: {texto_template}
    
    Gere todas as respostas passo a passo. Me informe claramente onde preencher no Word.
    
    REGRA OBRIGATÓRIA PARA A ETAPA 5 (MEMORIAL ANALÍTICO):
    Escreva a Etapa 5 seguindo estritamente o padrão abaixo com títulos em **negrito**:
    
    **Resumo:** [1 parágrafo]
    **Contextualização do desafio:** [Quem? Onde? Situação?]
    **Análise:** [Conceitos da disciplina, com exemplos]
    **Propostas de solução:** [Recomendações e teorias]
    **Conclusão reflexiva:** [O que foi aprendido]
    **Referências:** [ABNT]
    **Autoavaliação:** [Processo de estudo]
    O texto da Etapa 5 NÃO pode passar de 6000 caracteres.
    """
    resposta = modelo.generate_content(prompt)
    return resposta.text

# =========================================================
# ROTAS WEB (FLASK)
# =========================================================
@app.route('/')
def index():
    # Busca dinamicamente os modelos disponíveis na API
    modelos_disponiveis = []
    if CHAVE_API:
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    modelos_disponiveis.append(m.name.replace('models/', ''))
        except Exception as e:
            print(f"Erro ao listar modelos: {e}")
            
    # Fallback caso dê algum erro na listagem (garante que a página abra)
    if not modelos_disponiveis:
        modelos_disponiveis = ["gemini-2.5-flash", "gemini-2.5-pro"]
        
    # Manda a lista para o HTML
    return render_template('index.html', modelos=modelos_disponiveis)

@app.route('/processar', methods=['POST'])
def processar():
    try:
        # Garante que a API esteja configurada antes de gerar o conteúdo
        if not CHAVE_API:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            
        ferramenta = request.form.get('ferramenta')
        modelo_escolhido = request.form.get('modelo')
        texto_tema = request.form.get('tema')
        arquivo_upload = request.files['arquivo']
        
        if not arquivo_upload or arquivo_upload.filename == '':
            return "Erro: Você esqueceu de anexar o arquivo Word!", 400
        if not texto_tema:
            return "Erro: Você esqueceu de colar o tema do desafio!", 400

        arquivo_memoria = io.BytesIO(arquivo_upload.read())

        if ferramenta == 'preenchedor':
            respostas_geradas = gerar_respostas_ia_preenchedor(texto_tema, modelo_escolhido)
            if respostas_geradas:
                documento_pronto = preencher_template_inteligente(arquivo_memoria, respostas_geradas)
                return send_file(
                    documento_pronto, 
                    as_attachment=True, 
                    download_name="Desafio_Preenchido.docx",
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
        
        elif ferramenta == 'gabarito':
            texto_do_template = extrair_texto_docx(arquivo_memoria)
            resposta_ia = gerar_resolucao_inteligente_gabarito(texto_do_template, texto_tema, modelo_escolhido)
            if resposta_ia:
                documento_pronto = criar_gabarito_word(resposta_ia)
                return send_file(
                    documento_pronto, 
                    as_attachment=True, 
                    download_name="Gabarito_Desafio.docx",
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
                
        return "Erro na geração do documento com a IA.", 500
        
    except Exception as e:
        return f"Ocorreu um erro inesperado: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True)
