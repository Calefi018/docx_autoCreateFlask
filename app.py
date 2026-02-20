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
# FUNÇÕES DA FERRAMENTA 1: PREENCHEDOR CLÁSSICO (COM TAGS)
# =========================================================
def preencher_template_com_tags(arquivo_template, dicionario_dados):
    """Substitui as tags {{CHAVE}} pelo texto gerado, mantendo as caixas intactas."""
    doc = Document(arquivo_template)

    # 1. Substitui no corpo do texto normal
    for paragrafo in doc.paragraphs:
        for marcador, texto_novo in dicionario_dados.items():
            if marcador in paragrafo.text:
                paragrafo.text = paragrafo.text.replace(marcador, texto_novo)

    # 2. Substitui dentro de tabelas e caixas de texto
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for paragrafo in celula.paragraphs:
                    for marcador, texto_novo in dicionario_dados.items():
                        if marcador in paragrafo.text:
                            paragrafo.text = paragrafo.text.replace(marcador, texto_novo)

    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida

def gerar_respostas_ia_tags(texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Atue como um especialista acadêmico ajudando um estudante universitário.
    Gere as respostas originais e sem plágio para preencher um Desafio Profissional.
    
    REGRA MÁXIMA DE COMPORTAMENTO:
    NÃO use saudações. NÃO use formatação Markdown (como **negrito** ou *itálico*). 
    Retorne APENAS texto limpo.
    
    FORMATO DE SAÍDA OBRIGATÓRIO:
    Retorne APENAS um objeto JSON válido, contendo EXATAMENTE as chaves abaixo (SEM as chaves duplas no nome da variável).
    {{
        "ASPECTO_1": "texto curto do aspecto 1",
        "POR_QUE_1": "justificativa do aspecto 1",
        "ASPECTO_2": "texto curto do aspecto 2",
        "POR_QUE_2": "justificativa do aspecto 2",
        "ASPECTO_3": "texto curto do aspecto 3",
        "POR_QUE_3": "justificativa do aspecto 3",
        "CONCEITOS_TEORICOS": "Lista de conceitos...",
        "RESP_AUTORRESP": "Explicação teórica...",
        "RESP_PILARES": "Explicação teórica...",
        "RESP_SOLUCOES": "Soluções recomendadas...",
        "RESUMO_MEMORIAL": "Resumo...",
        "CONTEXTO_MEMORIAL": "Contextualização...",
        "ANALISE_MEMORIAL": "Análise...",
        "PROPOSTAS_MEMORIAL": "Propostas...",
        "CONCLUSAO_MEMORIAL": "Conclusão...",
        "AUTOAVALIACAO_MEMORIAL": "Autoavaliação..."
    }}
    
    TEMA/CASO DO DESAFIO:
    {texto_tema}
    """
    try:
        resposta = modelo.generate_content(prompt)
        texto_limpo = resposta.text.strip().replace("```json", "").replace("```", "")
        dicionario_dados = json.loads(texto_limpo)
        
        # Blindagem: Recria as tags {{ }} para o Python achar no Word e remove sujeiras
        dicionario_higienizado = {}
        for chave, texto_gerado in dicionario_dados.items():
            if isinstance(texto_gerado, str):
                texto_gerado = texto_gerado.replace("{", "").replace("}", "").replace("[", "").replace("]", "").replace("*", "").strip()
            else:
                texto_gerado = str(texto_gerado)
                
            chave_limpa = chave.replace("{", "").replace("}", "").strip()
            chave_marcador = f"{{{{{chave_limpa}}}}}"
            dicionario_higienizado[chave_marcador] = texto_gerado
            
        return dicionario_higienizado
    except Exception as e:
        print(f"Erro IA: {e}")
        return None

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
    
    REGRA MÁXIMA DE COMPORTAMENTO:
    NÃO use NENHUMA saudação (ex: "Olá", "Bem-vindo"). 
    NÃO use frases introdutórias. Vá DIRETO AO PONTO. Comece direto com "--- ETAPA 1".
    
    REGRA DE FORMATAÇÃO (MARKDOWN):
    Use '---' (três traços) em uma linha separada para criar uma linha divisória antes de cada etapa.
    Use **negrito** para destacar tópicos.
    O texto da Etapa 5 NÃO pode passar de 6000 caracteres e deve conter os tópicos em **negrito**.
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
            respostas_geradas = gerar_respostas_ia_tags(texto_tema, modelo_escolhido)
            if respostas_geradas:
                documento_pronto = preencher_template_com_tags(arquivo_memoria, respostas_geradas)
                return send_file(
                    documento_pronto, 
                    as_attachment=True, 
                    download_name="Desafio_Com_Tags_Preenchido.docx",
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            else:
                return jsonify({"erro": "Falha ao gerar respostas com a IA."}), 500
        
        elif ferramenta == 'gabarito':
            texto_do_template = extrair_texto_docx(arquivo_memoria)
            resposta_ia = gerar_resolucao_inteligente_gabarito(texto_do_template, texto_tema, modelo_escolhido)
            if resposta_ia:
                return jsonify({"tipo": "texto", "conteudo": resposta_ia})
                
        return jsonify({"erro": "Opção inválida."}), 500
        
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
