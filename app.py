import os
import io
import json
import re
from flask import Flask, render_template, request, send_file, jsonify
from docx import Document
import google.generativeai as genai

app = Flask(__name__)

CHAVE_API = os.environ.get("GEMINI_API_KEY")
if CHAVE_API:
    genai.configure(api_key=CHAVE_API)

# =========================================================
# FUNÇÕES DA FERRAMENTA 1: PREENCHEDOR INTELIGENTE
# =========================================================
def preencher_template_inteligente(arquivo_template, respostas_json):
    doc = Document(arquivo_template)
    
    def substituir_texto_formatado(paragrafo, texto_resposta):
        paragrafo.text = "" 
        texto_limpo = texto_resposta.replace("\n* ", "\n- ").replace("\n*", "\n- ")
        
        partes = texto_limpo.split('**')
        for i, parte in enumerate(partes):
            if i % 2 == 1:
                paragrafo.add_run(parte).bold = True
            else:
                paragrafo.add_run(parte)

    # Lê as tabelas PRIMEIRO (Onde geralmente ficam as caixas de resposta)
    todos_paragrafos = []
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for p in celula.paragraphs:
                    todos_paragrafos.append(p)

    # Adiciona os parágrafos soltos DEPOIS
    todos_paragrafos.extend(list(doc.paragraphs))

    preenchidos = {"etapa_2": False, "etapa_3": False, "etapa_4": False, "etapa_5": False}
    
    for p in todos_paragrafos:
        texto = p.text.lower().strip()
        
        if ("estudante, escreva aqui" in texto or "escreva aqui os três aspectos" in texto or "aspecto 1:" in texto) and not preenchidos["etapa_2"]:
            substituir_texto_formatado(p, respostas_json.get('etapa_2', ''))
            preenchidos["etapa_2"] = True
            
        elif "registre aqui os conceitos" in texto and not preenchidos["etapa_3"]:
            substituir_texto_formatado(p, respostas_json.get('etapa_3', ''))
            preenchidos["etapa_3"] = True
            
        elif "aplique aqui os conceitos" in texto and not preenchidos["etapa_4"]:
            substituir_texto_formatado(p, respostas_json.get('etapa_4', ''))
            preenchidos["etapa_4"] = True
            
        elif ("registre aqui seu memorial" in texto or "escreva aqui seu memorial" in texto) and not preenchidos["etapa_5"]:
            substituir_texto_formatado(p, respostas_json.get('etapa_5', ''))
            preenchidos["etapa_5"] = True

    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida

def gerar_respostas_ia_preenchedor(texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Você é um especialista acadêmico ajudando um estudante universitário.
    Gere as respostas originais e sem plágio para preencher as Etapas 2, 3, 4 e 5 do trabalho.
    
    REGRA MÁXIMA DE COMPORTAMENTO:
    NÃO use saudações ("Olá", "Bem-vindo"). Retorne APENAS o JSON.
    
    REGRA DE FORMATAÇÃO:
    NÃO use asteriscos simples (*) para listas. Use sempre traços (-). Use asteriscos duplos (**) APENAS para negrito.
    
    FORMATO DE SAÍDA OBRIGATÓRIO:
    Retorne APENAS um objeto JSON válido.
    {{
        "etapa_2": "Texto da resposta completa...",
        "etapa_3": "Texto da resposta completa...",
        "etapa_4": "Texto da resposta completa...",
        "etapa_5": "**Resumo:** ...\\n**Contextualização:** ..."
    }}
    DESCRIÇÃO DO TEMA/CASO DO DESAFIO:
    {texto_tema}
    """
    resposta = modelo.generate_content(prompt)
    
    # EXTRAÇÃO SEGURA COM REGEX (Ignora qualquer lixo antes ou depois das chaves do JSON)
    match = re.search(r'\{.*\}', resposta.text, re.DOTALL)
    if not match:
        raise ValueError("A IA não retornou um formato JSON válido.")
        
    texto_limpo = match.group(0)
    return json.loads(texto_limpo)

# =========================================================
# FUNÇÕES DA FERRAMENTA 2: GERADOR UNIVERSAL (GABARITO)
# =========================================================
def extrair_texto_docx(arquivo_upload):
    doc = Document(arquivo_upload)
    texto_completo = []
    
    for p in doc.paragraphs:
        if p.text.strip():
            texto_completo.append(p.text.strip())
            
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for p in celula.paragraphs:
                    if p.text.strip():
                        texto_completo.append(p.text.strip())
                        
    return "\n".join(texto_completo)

def gerar_resolucao_inteligente_gabarito(texto_template, texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Atue como um especialista acadêmico ajudando um estudante a resolver um Desafio Profissional.
    
    TEMA/CASO: {texto_tema}
    TEMPLATE: {texto_template}
    
    Vá DIRETO AO PONTO. Comece o texto diretamente com "--- ETAPA 1".
    Use '---' (três traços) em uma linha separada para criar uma linha divisória.
    Use **negrito** para destacar tópicos.
    """
    resposta = modelo.generate_content(prompt)
    return resposta.text

# =========================================================
# ROTAS WEB
# =========================================================
@app.route('/')
def index():
    modelos_disponiveis = []
    if CHAVE_API:
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    modelos_disponiveis.append(m.name.replace('models/', ''))
        except:
            pass
    if not modelos_disponiveis:
        modelos_disponiveis = ["gemini-2.5-flash", "gemini-2.5-pro"]
    return render_template('index.html', modelos=modelos_disponiveis)

@app.route('/processar', methods=['POST'])
def processar():
    try:
        # Tenta pegar a chave do ambiente se não estiver configurada
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"erro": "A chave da API do Gemini (GEMINI_API_KEY) não está configurada no servidor."}), 500
            
        genai.configure(api_key=api_key)
            
        ferramenta = request.form.get('ferramenta')
        modelo_escolhido = request.form.get('modelo')
        texto_tema = request.form.get('tema')
        
        if 'arquivo' not in request.files:
             return jsonify({"erro": "Nenhum arquivo enviado na requisição."}), 400
             
        arquivo_upload = request.files['arquivo']
        if not arquivo_upload.filename.endswith('.docx'):
            return jsonify({"erro": "O arquivo deve ser um .docx."}), 400
        
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
                return jsonify({"tipo": "texto", "conteudo": resposta_ia})
                
        return jsonify({"erro": "A IA não conseguiu gerar uma resposta."}), 500
        
    except Exception as e:
        # Envia O ERRO REAL para o frontend mostrar na tela
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
