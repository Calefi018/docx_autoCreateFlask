import os
import io
import json
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
        partes = texto_resposta.split('**')
        for i, parte in enumerate(partes):
            if i % 2 == 1:
                paragrafo.add_run(parte).bold = True
            else:
                paragrafo.add_run(parte)

    todos_paragrafos = list(doc.paragraphs)
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for p in celula.paragraphs:
                    todos_paragrafos.append(p)

    preenchidos = {"etapa_2": False, "etapa_3": False, "etapa_4": False, "etapa_5": False}
    
    for p in todos_paragrafos:
        texto = p.text.lower().strip()
        
        # Etapa 2: Busca ampliada para garantir que acerta a caixa
        if ("aspecto 1:" in texto or "escreva aqui os três aspectos" in texto or "estudante, escreva aqui." in texto) and not preenchidos["etapa_2"]:
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
    
    REGRA OBRIGATÓRIA DA ETAPA 5:
    Siga estritamente este padrão com os títulos em **negrito**:
    **Resumo:** [1 parágrafo]
    **Contextualização do desafio:** [Quem? Onde? Situação?]
    **Análise:** [Conceitos e exemplos]
    **Propostas de solução:** [Recomendações]
    **Conclusão reflexiva:** [Aprendizado]
    **Referências:** [ABNT]
    **Autoavaliação:** [Processo de estudo]
    
    FORMATO DE SAÍDA OBRIGATÓRIO:
    Retorne APENAS um objeto JSON válido.
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

def gerar_resolucao_inteligente_gabarito(texto_template, texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Atue como um especialista acadêmico ajudando um estudante a resolver um Desafio Profissional.
    
    TEMA/CASO: {texto_tema}
    TEMPLATE: {texto_template}
    
    Gere as respostas passo a passo. Informe claramente onde preencher no Word (Ex: "Na Etapa 2, escreva isso:").
    Use a formatação Markdown (**negrito**) para destacar títulos.
    O texto da Etapa 5 NÃO pode passar de 6000 caracteres.
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
        if not CHAVE_API:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            
        ferramenta = request.form.get('ferramenta')
        modelo_escolhido = request.form.get('modelo')
        texto_tema = request.form.get('tema')
        arquivo_upload = request.files['arquivo']
        
        if not arquivo_upload or not texto_tema:
            return jsonify({"erro": "Arquivo ou tema ausentes."}), 400

        arquivo_memoria = io.BytesIO(arquivo_upload.read())

        if ferramenta == 'preenchedor':
            respostas_geradas = gerar_respostas_ia_preenchedor(texto_tema, modelo_escolhido)
            if respostas_geradas:
                documento_pronto = preencher_template_inteligente(arquivo_memoria, respostas_geradas)
                # Retorna o arquivo diretamente
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
                # Retorna apenas o texto JSON para renderizar na tela
                return jsonify({"tipo": "texto", "conteudo": resposta_ia})
                
        return jsonify({"erro": "Falha ao gerar conteúdo."}), 500
        
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
