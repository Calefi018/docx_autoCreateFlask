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
    doc = Document(arquivo_template)

    def processar_paragrafo(paragrafo):
        texto_original = paragrafo.text
        tem_tag = False
        
        for marcador, texto_novo in dicionario_dados.items():
            if marcador in texto_original:
                # Substitui a tag pelo texto limpo (O JSON Mode já manda o \n nativo correto)
                texto_original = texto_original.replace(marcador, str(texto_novo))
                tem_tag = True
                
        if tem_tag:
            paragrafo.clear()
            
            # TRADUTOR DE MARKDOWN PARA WORD (Lida com \n real e **)
            linhas = texto_original.split('\n')
            for i, linha in enumerate(linhas):
                partes = linha.split('**')
                for j, parte in enumerate(partes):
                    if parte: 
                        run = paragrafo.add_run(parte)
                        if j % 2 == 1: 
                            run.bold = True
                
                # Adiciona o 'Enter' real no Word se não for a última linha
                if i < len(linhas) - 1:
                    paragrafo.add_run('\n')

    # Varre todo o documento
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

def gerar_respostas_ia_tags(texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Atue como um especialista acadêmico resolvendo um Desafio Profissional.
    
    REGRA MÁXIMA: 
    Vá DIRETO ao conteúdo. NÃO use saudações.
    Para destacar palavras, use **negrito**. NUNCA use asteriscos simples (*) para listas, use traço (-).
    Para pular linha, use a quebra de linha normal.
    
    TEMA/CASO DO DESAFIO:
    {texto_tema}
    
    Você DEVE retornar as seguintes chaves no JSON:
    "ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3",
    "CONCEITOS_TEORICOS", "RESP_AUTORRESP", "RESP_PILARES", "RESP_SOLUCOES",
    "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", 
    "PROPOSTAS_MEMORIAL", "CONCLUSAO_MEMORIAL", "AUTOAVALIACAO_MEMORIAL".
    """
    try:
        # A MÁGICA: Força o Google a retornar 100% formato JSON nativo (Inquebrável)
        resposta = modelo.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        # Como o formato é garantido, podemos carregar direto
        dicionario_dados = json.loads(resposta.text)
        
        # Recria as tags {{ }} para o Python achar no Word
        dicionario_higienizado = {}
        for chave, texto_gerado in dicionario_dados.items():
            chave_limpa = chave.replace("{", "").replace("}", "").strip()
            chave_marcador = f"{{{{{chave_limpa}}}}}"
            dicionario_higienizado[chave_marcador] = str(texto_gerado).strip()
            
        return dicionario_higienizado
    except Exception as e:
        raise Exception(f"Falha na IA (Tags): {str(e)}")

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
    NÃO use NENHUMA saudação (ex: "Olá"). Comece o texto diretamente com "--- ETAPA 1".
    
    REGRA DE FORMATAÇÃO (MARKDOWN):
    Use '---' (três traços) em uma linha separada para criar uma linha divisória antes de cada etapa.
    Use **negrito** para destacar tópicos e títulos.
    """
    try:
        resposta = modelo.generate_content(prompt)
        if not resposta.parts:
            raise Exception("A resposta foi bloqueada pelos filtros de segurança do Google.")
        return resposta.text
    except Exception as e:
        raise Exception(f"Falha na IA (Gabarito): {str(e)}")

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
            return jsonify({"erro": "Arquivo Word ou tema do desafio não foram enviados."}), 400

        arquivo_memoria = io.BytesIO(arquivo_upload.read())

        if ferramenta == 'preenchedor':
            respostas_geradas = gerar_respostas_ia_tags(texto_tema, modelo_escolhido)
            if respostas_geradas:
                documento_pronto = preencher_template_com_tags(arquivo_memoria, respostas_geradas)
                return send_file(
                    documento_pronto, 
                    as_attachment=True, 
                    download_name="Desafio_Preenchido_Perfeito.docx",
                    mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
        
        elif ferramenta == 'gabarito':
            texto_do_template = extrair_texto_docx(arquivo_memoria)
            resposta_ia = gerar_resolucao_inteligente_gabarito(texto_do_template, texto_tema, modelo_escolhido)
            if resposta_ia:
                return jsonify({"tipo": "texto", "conteudo": resposta_ia})
                
        return jsonify({"erro": "Opção inválida selecionada."}), 400
        
    except Exception as e:
        # Agora sim o Python vai mandar o erro REAL para a tela do site
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
