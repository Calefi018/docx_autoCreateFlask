import os
import io
import re
import base64
import traceback
from flask import Flask, render_template, request, jsonify
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
            titulos_memorial = [
                "Resumo", "Contextualização do desafio", "Análise", 
                "Propostas de solução", "Conclusão reflexiva", "Referências", "Autoavaliação"
            ]
            for t in titulos_memorial:
                if texto_original.strip().startswith(t):
                    texto_original = texto_original.replace(t, f"**{t}**\n", 1)
            
            titulos_aspectos = ["Aspecto 1:", "Aspecto 2:", "Aspecto 3:", "Por quê:"]
            for t in titulos_aspectos:
                if t in texto_original:
                    if "Por quê:" in t:
                        texto_original = texto_original.replace(t, f"\n**{t}** ")
                    else:
                        texto_original = texto_original.replace(t, f"**{t}** ")
                        
            if "?" in texto_original and "Por quê:" not in texto_original:
                partes = texto_original.split("?", 1)
                pergunta = partes[0].strip()
                if 10 < len(pergunta) < 150 and not pergunta.startswith("**"):
                    texto_original = f"**{pergunta}?**\n" + partes[1].lstrip()

            texto_original = texto_original.replace("**\n ", "**\n")
            texto_original = texto_original.replace(":**\n:", ":**\n")

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

def gerar_respostas_ia_tags(texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Você é um Especialista Acadêmico Sênior com Doutorado. Sua missão é resolver um Desafio Profissional Universitário.
    
    REGRA DE OURO (QUALIDADE EXTREMA): 
    É EXPRESSAMENTE PROIBIDO ser raso ou breve. Suas respostas devem ser DENSAS, PROFUNDAS e usar vocabulário técnico acadêmico rigoroso. Cada justificativa e análise deve ter múltiplas linhas de argumentação fundamentada.
    
    REGRA DE ESTRUTURA (ANTI-ERRO):
    NÃO USE FORMATO JSON. Você DEVE retornar o texto preenchendo as caixas delimitadoras exatas abaixo.
    É ESTRITAMENTE PROIBIDO usar frases introdutórias (como "Segue a lista").
    Para destacar conceitos, use **negrito**. Para tópicos, use o traço (-). Pule linhas com Enter.
    
    TEMA/CASO DO DESAFIO (com as referências no final):
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
    
    [START_ANALISE_CONCEITO_1]
    Análise teórica profunda respondendo como o conceito principal explica o que aconteceu na situação...
    [END_ANALISE_CONCEITO_1]
    
    [START_ENTENDIMENTO_TEORICO]
    Análise teórica densa respondendo o que a teoria ajuda a entender sobre o problema central...
    [END_ENTENDIMENTO_TEORICO]
    
    [START_SOLUCOES_TEORICAS]
    Apresente um plano de ação robusto respondendo que soluções possíveis a teoria aponta e por que fazem sentido...
    [END_SOLUCOES_TEORICAS]
    
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
    
    [START_REFERENCIAS_ADICIONAIS]
    Localize as referências bibliográficas e fontes que foram informadas no texto do TEMA/CASO DO DESAFIO e liste-as aqui rigorosamente no padrão ABNT. Se não houver referências coladas, cite os conceitos teóricos gerais utilizados.
    [END_REFERENCIAS_ADICIONAIS]
    
    [START_AUTOAVALIACAO_MEMORIAL]
    Escreva em primeira pessoa ("eu"). Atue como o ALUNO humano que acabou de fazer este trabalho. Reflita sobre o que VOCÊ (aluno) aprendeu com a teoria da disciplina e como foi o desafio de aplicar os conceitos teóricos na prática do caso apresentado. 
    ATENÇÃO: É EXPRESSAMENTE PROIBIDO mencionar regras de formatação, caixas delimitadoras, JSON, ou inteligência artificial. 
    É EXPRESSAMENTE PROIBIDO ATRIBUIR NOTAS NUMÉRICAS A SI MESMO.
    [END_AUTOAVALIACAO_MEMORIAL]
    """
    try:
        resposta = modelo.generate_content(prompt)
        texto_ia = resposta.text
        
        chaves = [
            "ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3",
            "CONCEITOS_TEORICOS", "ANALISE_CONCEITO_1", "ENTENDIMENTO_TEORICO", "SOLUCOES_TEORICAS",
            "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL",
            "CONCLUSAO_MEMORIAL", "REFERENCIAS_ADICIONAIS", "AUTOAVALIACAO_MEMORIAL"
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
    TEMA/CASO (com as referências no final): {texto_tema}
    TEMPLATE: {texto_template}
    
    REGRA MÁXIMA DE COMPORTAMENTO E QUALIDADE:
    - NÃO use NENHUMA saudação ou despedida. Vá DIRETO AO PONTO.
    - É EXPRESSAMENTE PROIBIDO gerar conteúdo raso. Exijo parágrafos densos, análises profundas.
    - É EXPRESSAMENTE PROIBIDO ATRIBUIR NOTAS NUMÉRICAS A SI MESMO NA AUTOAVALIAÇÃO.
    
    ESTRUTURA VISUAL OBRIGATÓRIA (SIGA ESTE PADRÃO MARKDOWN):
    
    Pré-visualização do Resultado:
    Olá! Serei seu especialista acadêmico. Vamos preencher o template passo a passo.
    
    ---
    **Na Etapa 1, você deve apenas ler e compreender o desafio.**
    
    ---
    **Na Etapa 2 (Materiais de referência), escreva isso:**
    
    **1. O que chamou atenção:** **[Aspecto 1]**
    - **Por quê:** [Justificativa densa de no mínimo 4 linhas]
    
    **2. O que chamou atenção:** **[Aspecto 2]**
    - **Por quê:** [Justificativa densa]
    
    **3. O que chamou atenção:** **[Aspecto 3]**
    - **Por quê:** [Justificativa densa]
    
    ---
    **Na Etapa 3 (Levantamento de conceitos), escreva isso:**
    
    - **[Nome do Conceito 1]:** [Definição extensa]
    - **[Nome do Conceito 2]:** [Definição extensa]
    
    ---
    **Na Etapa 4 (Aplicação dos conceitos), escreva isso:**
    
    - **Como o conceito explica o que aconteceu?**
      [Parágrafo analítico longo]
    - **O que a teoria ajuda a entender sobre o problema?**
      [Parágrafo profundo conectando sintomas e teorias]
    - **Que soluções a teoria aponta?**
      [Propostas práticas detalhadas]
      
    ---
    **Na Etapa 5 (Memorial Analítico), escreva isso:**
    
    **Resumo do que você descobriu:** [Parágrafo denso]
    **Contextualização do desafio:** [Quem? Onde? Qual a situação?]
    **Análise:** [Parágrafo profundo utilizando conceitos]
    **Propostas de solução:** [Recomendações detalhadas]
    **Conclusão reflexiva:** [O que aprendeu de forma madura]
    **Referências:** [Extraia as referências do texto do tema e formate em padrão ABNT]
    **Autoavaliação:** [Escreva em primeira pessoa ("eu"). Atue como o ALUNO humano que acabou de fazer este trabalho. Reflita sobre os estudos da disciplina. ATENÇÃO: É proibido citar inteligência artificial, regras de prompt ou dar nota a si mesmo.]
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
        arquivo_upload = request.files.get('arquivo')
        
        if not texto_tema:
            return jsonify({"erro": "O tema do desafio não foi enviado."}), 400

        # MÁGICA DO ARQUIVO LOCAL
        if arquivo_upload and arquivo_upload.filename != '':
            # O usuário anexou um arquivo manualmente
            arquivo_memoria = io.BytesIO(arquivo_upload.read())
        else:
            # O usuário deixou vazio, o sistema pega o arquivo padrão do Github/Render
            caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
            if not os.path.exists(caminho_padrao):
                return jsonify({"erro": "Você não anexou nenhum arquivo e o TEMPLATE_COM_TAGS.docx padrão não foi encontrado no servidor. Faça o upload dele no GitHub!"}), 400
            
            with open(caminho_padrao, 'rb') as f:
                arquivo_memoria = io.BytesIO(f.read())

        if ferramenta == 'preenchedor':
            respostas_geradas = gerar_respostas_ia_tags(texto_tema, modelo_escolhido)
            if respostas_geradas:
                documento_pronto = preencher_template_com_tags(arquivo_memoria, respostas_geradas)
                arquivo_bytes = documento_pronto.read()
                arquivo_base64 = base64.b64encode(arquivo_bytes).decode('utf-8')
                
                memorial_texto = f"""### Memorial Analítico

**Resumo**
{respostas_geradas.get('{{RESUMO_MEMORIAL}}', '')}

**Contextualização do desafio**
{respostas_geradas.get('{{CONTEXTO_MEMORIAL}}', '')}

**Análise**
{respostas_geradas.get('{{ANALISE_MEMORIAL}}', '')}

**Propostas de solução**
{respostas_geradas.get('{{PROPOSTAS_MEMORIAL}}', '')}

**Conclusão reflexiva**
{respostas_geradas.get('{{CONCLUSAO_MEMORIAL}}', '')}

**Referências**
{respostas_geradas.get('{{REFERENCIAS_ADICIONAIS}}', '')}

**Autoavaliação**
{respostas_geradas.get('{{AUTOAVALIACAO_MEMORIAL}}', '')}
"""
                return jsonify({
                    "tipo": "sucesso_tags",
                    "arquivo_base64": arquivo_base64,
                    "nome_arquivo": "Desafio_Preenchido_Academico.docx",
                    "memorial_texto": memorial_texto
                })
        
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
