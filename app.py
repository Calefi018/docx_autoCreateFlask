import os
import io
import re
import traceback
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
                texto_original = texto_original.replace(marcador, str(texto_novo))
                tem_tag = True
                
        if tem_tag:
            # ========================================================
            # AUTO FORMATADOR MÁGICO (Design idêntico ao Gabarito)
            # ========================================================
            
            # 1. Pula linha e bota negrito nos títulos da Etapa 5
            titulos_memorial = [
                "Resumo", "Contextualização do desafio", "Análise", 
                "Propostas de solução", "Conclusão reflexiva", "Referências", "Autoavaliação"
            ]
            for t in titulos_memorial:
                if texto_original.strip().startswith(t):
                    texto_original = texto_original.replace(t, f"**{t}**\n", 1)
            
            # 2. Bota negrito nos Aspectos e pula linha antes do "Por quê:"
            titulos_aspectos = ["Aspecto 1:", "Aspecto 2:", "Aspecto 3:", "Por quê:"]
            for t in titulos_aspectos:
                if t in texto_original:
                    if "Por quê:" in t:
                        texto_original = texto_original.replace(t, f"\n**{t}** ")
                    else:
                        texto_original = texto_original.replace(t, f"**{t}** ")
                        
            # 3. Auto-negrito e pulo de linha para as perguntas da Etapa 4
            if "?" in texto_original and "Por quê:" not in texto_original:
                partes = texto_original.split("?", 1)
                pergunta = partes[0].strip()
                if 10 < len(pergunta) < 150 and not pergunta.startswith("**"):
                    texto_original = f"**{pergunta}?**\n" + partes[1].lstrip()

            # Limpa espacinhos extras gerados pelas quebras de linha
            texto_original = texto_original.replace("**\n ", "**\n")
            texto_original = texto_original.replace(":**\n:", ":**\n")
            
            # ========================================================

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
    Você é um Especialista Acadêmico Sênior com Doutorado em Gestão. Sua missão é resolver um Desafio Profissional Universitário.
    
    REGRA DE OURO (QUALIDADE EXTREMA): 
    É EXPRESSAMENTE PROIBIDO ser raso ou breve. Suas respostas devem ser DENSAS, PROFUNDAS e usar vocabulário técnico acadêmico rigoroso. Cada justificativa e análise deve ter múltiplas linhas de argumentação fundamentada.
    
    REGRA DE ESTRUTURA (ANTI-ERRO E ANTI-FALAÇÃO):
    NÃO USE FORMATO JSON. Você DEVE retornar o texto preenchendo as caixas delimitadoras exatas abaixo.
    É ESTRITAMENTE PROIBIDO usar frases introdutórias (como "Segue a lista", "Aqui estão as respostas").
    Para destacar conceitos, use **negrito**. Para tópicos, use o traço (-). Pule linhas normalmente com Enter.
    
    TEMA/CASO DO DESAFIO:
    {texto_tema}
    
    GERAÇÃO OBRIGATÓRIA (Crie textos extensos dentro de cada delimitador e NADA MAIS):
    
    [START_ASPECTO_1]
    Descreva o aspecto 1 de forma técnica e profunda...
    [END_ASPECTO_1]
    
    [START_POR_QUE_1]
    Justifique o aspecto 1 com uma análise densa de pelo menos 4 linhas...
    [END_POR_QUE_1]
    
    [START_ASPECTO_2]
    Descreva o aspecto 2 de forma técnica...
    [END_ASPECTO_2]
    
    [START_POR_QUE_2]
    Justifique o aspecto 2 com uma análise densa de pelo menos 4 linhas...
    [END_POR_QUE_2]
    
    [START_ASPECTO_3]
    Descreva o aspecto 3 de forma técnica...
    [END_ASPECTO_3]
    
    [START_POR_QUE_3]
    Justifique o aspecto 3 com uma análise densa de pelo menos 4 linhas...
    [END_POR_QUE_3]
    
    [START_CONCEITOS_TEORICOS]
    - **[Nome do Conceito 1]:** [Explicação teórica longa e detalhada sobre como se aplica ao caso]
    - **[Nome do Conceito 2]:** [Explicação teórica longa e detalhada...]
    [END_CONCEITOS_TEORICOS]
    
    [START_RESP_AUTORRESP]
    Análise teórica profunda e extensa aplicada ao cenário específico...
    [END_RESP_AUTORRESP]
    
    [START_RESP_PILARES]
    Análise teórica densa sobre o problema central...
    [END_RESP_PILARES]
    
    [START_RESP_SOLUCOES]
    Apresente um plano de ação robusto listando etapas detalhadas...
    [END_RESP_SOLUCOES]
    
    [START_RESUMO_MEMORIAL]
    Resumo executivo denso e bem articulado...
    [END_RESUMO_MEMORIAL]
    
    [START_CONTEXTO_MEMORIAL]
    Contextualização rica detalhando a complexidade da situação...
    [END_CONTEXTO_MEMORIAL]
    
    [START_ANALISE_MEMORIAL]
    Análise aprofundada com múltiplos parágrafos, interligando conceitos da disciplina...
    [END_ANALISE_MEMORIAL]
    
    [START_PROPOSTAS_MEMORIAL]
    Recomendações técnicas detalhadas e justificadas por teorias...
    [END_PROPOSTAS_MEMORIAL]
    
    [START_CONCLUSAO_MEMORIAL]
    Conclusão reflexiva madura sobre o aprendizado do caso...
    [END_CONCLUSAO_MEMORIAL]
    
    [START_AUTOAVALIACAO_MEMORIAL]
    Autoavaliação crítica do aluno, evidenciando amadurecimento acadêmico...
    [END_AUTOAVALIACAO_MEMORIAL]
    """
    try:
        resposta = modelo.generate_content(prompt)
        texto_ia = resposta.text
        
        chaves = [
            "ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3",
            "CONCEITOS_TEORICOS", "RESP_AUTORRESP", "RESP_PILARES", "RESP_SOLUCOES",
            "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL",
            "CONCLUSAO_MEMORIAL", "AUTOAVALIACAO_MEMORIAL"
        ]
        
        dicionario_higienizado = {}
        for chave in chaves:
            padrao = rf"\[START_{chave}\](.*?)\[END_{chave}\]"
            match = re.search(padrao, texto_ia, re.DOTALL)
            if match:
                dicionario_higienizado[f"{{{{{chave}}}}}"] = match.group(1).strip()
            else:
                dicionario_higienizado[f"{{{{{chave}}}}}"] = "" 
                
        return dicionario_higienizado
    except Exception as e:
        raise Exception(f"Falha de geração na IA: {str(e)}")

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
    Atue como um Especialista Acadêmico Sênior resolvendo um Desafio Profissional.
    TEMA/CASO: {texto_tema}
    TEMPLATE: {texto_template}
    
    REGRA MÁXIMA DE COMPORTAMENTO E QUALIDADE:
    - NÃO use NENHUMA saudação ou despedida. Vá DIRETO AO PONTO.
    - É EXPRESSAMENTE PROIBIDO gerar conteúdo raso. Exijo parágrafos densos, análises profundas e vocabulário acadêmico de alto nível.
    
    ESTRUTURA VISUAL OBRIGATÓRIA (SIGA EXATAMENTE ESTE PADRÃO MARKDOWN):
    
    Pré-visualização do Resultado:
    Olá! Serei seu especialista acadêmico neste Desafio Profissional. Vamos preencher o template passo a passo.
    
    ---
    **Na Etapa 1 (Apresentação do Desafio Profissional), você deve apenas ler e compreender o desafio.**
    
    ---
    **Na Etapa 2 (Materiais de referência - ambientação), escreva isso:**
    
    **1. O que chamou atenção:** **[Escreva o Conceito do Aspecto 1]**
    - **Por quê:** [Justificativa técnica e profunda de no mínimo 4 linhas]
    
    **2. O que chamou atenção:** **[Escreva o Conceito do Aspecto 2]**
    - **Por quê:** [Justificativa técnica e profunda de no mínimo 4 linhas]
    
    **3. O que chamou atenção:** **[Escreva o Conceito do Aspecto 3]**
    - **Por quê:** [Justificativa técnica e profunda de no mínimo 4 linhas]
    
    ---
    **Na Etapa 3 (Levantamento de conceitos teóricos), escreva isso:**
    
    - **[Nome do Conceito 1]:** [Definição extensa e elaborada mostrando como se aplica ao caso]
    - **[Nome do Conceito 2]:** [Definição extensa e elaborada...]
    - **[Nome do Conceito 3]:** [Definição extensa e elaborada...]
    
    ---
    **Na Etapa 4 (Aplicação dos conceitos teóricos ao Desafio Profissional), escreva isso:**
    
    - **Como o conceito de [Conceito Principal] explica o que aconteceu na situação?**
      [Parágrafo analítico longo e detalhado dissecando o problema]
    - **O que a teoria nos ajuda a entender sobre o problema central?**
      [Parágrafo profundo conectando sintomas e teorias]
    - **Que soluções possíveis a teoria aponta (e por que elas fazem sentido)?**
      [Propostas práticas detalhadas fundamentadas na teoria]
      
    ---
    **Na Etapa 5 (Memorial Analítico), escreva isso:**
    
    **Resumo do que você descobriu:** [Parágrafo denso]
    **Contextualização do desafio:** [Quem? Onde? Qual a situação? Parágrafo denso]
    **Análise:** [Parágrafo profundo de pelo menos 6 linhas utilizando conceitos para explicar a situação]
    **Propostas de solução:** [Recomendações detalhadas. Pelo menos 2 parágrafos robustos]
    **Conclusão reflexiva:** [O que aprendeu de forma madura. Pelo menos 2 parágrafos]
    **Referências:** [Liste no padrão ABNT]
    **Autoavaliação:** [Análise crítica sobre o próprio processo de estudo]
    """
    try:
        resposta = modelo.generate_content(prompt)
        return resposta.text
    except Exception as e:
        raise Exception(f"Falha na IA (Gabarito): {str(e)}")

# =========================================================
# ROTAS WEB
# =========================================================
@app.route('/')
def index():
    try:
        modelos_disponiveis = []
        if CHAVE_API:
            try:
                for m in genai.list_models():
                    if 'generateContent' in m.supported_generation_methods:
                        modelos_disponiveis.append(m.name.replace('models/', ''))
            except:
                pass
                
        if "gemini-1.5-flash" not in modelos_disponiveis:
            modelos_disponiveis.insert(0, "gemini-1.5-flash")
            
        return render_template('index.html', modelos=modelos_disponiveis)
    except Exception as e:
        return f"Erro crítico ao carregar: {str(e)}", 500

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
        traceback.print_exc()
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
