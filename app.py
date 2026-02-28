import os
import io
import re
import base64
import traceback
import json
import requests 
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, abort, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from docx import Document

from google import genai 
from google.genai import types 

app = Flask(__name__)

# =========================================================
# TELA DE RAIO-X (Tratamento de Erros)
# =========================================================
@app.errorhandler(Exception)
def handle_exception(e):
    try: 
        db.session.rollback()
    except Exception: 
        pass
    
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): 
        return e
        
    return f"""
    <div style="font-family: sans-serif; padding: 20px; background: #262730; color: #E1E4E8; height: 100vh;">
        <h1 style="color: #FF4B4B;">🚨 Falha Crítica Detectada</h1>
        <p>O sistema encontrou um erro interno.</p>
        <div style="background: #1A1C23; padding: 15px; border-radius: 5px; color: #FFC107; font-family: monospace; overflow-x: auto;">
            <b>{type(e).__name__}</b>: {str(e)}
        </div>
        <p style="margin-top: 20px;">Tire um print e envie para análise.</p>
        <a href="/" style="color: #2196F3; text-decoration: none;">⬅️ Tentar Voltar</a>
    </div>
    """, 500

# =========================================================
# CONFIGURAÇÕES DO BANCO DE DADOS E SEGURANÇA
# =========================================================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-super-secreta-mude-depois')

db_url = os.environ.get('DATABASE_URL', 'sqlite:///clientes.db')
if db_url.startswith("postgres://"): 
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True, 
    "pool_recycle": 60, 
    "pool_timeout": 30
}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para acessar."

CHAVE_API_GOOGLE = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=CHAVE_API_GOOGLE) if CHAVE_API_GOOGLE else None

CHAVE_OPENROUTER = os.environ.get("OPENAI_API_KEY")

# =========================================================
# MODELOS DO BANCO DE DADOS
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='admin') 
    expiration_date = db.Column(db.Date, nullable=True)
    alunos = db.relationship('Aluno', backref='responsavel', lazy=True)

class Aluno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    curso = db.Column(db.String(100))
    telefone = db.Column(db.String(20))
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='Produção') 
    valor = db.Column(db.Float, default=70.0) 
    
    documentos = db.relationship('Documento', backref='aluno', lazy=True, cascade="all, delete-orphan")
    temas = db.relationship('TemaTrabalho', backref='aluno', lazy=True, cascade="all, delete-orphan")

class Documento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    dados_arquivo = db.Column(db.LargeBinary, nullable=False) 
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

class TemaTrabalho(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    titulo = db.Column(db.String(255), nullable=True) 
    texto = db.Column(db.Text, nullable=False)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

class PromptConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    is_default = db.Column(db.Boolean, default=False)

class RegistroUso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    modelo_usado = db.Column(db.String(100))

class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    whatsapp_template = db.Column(
        db.Text, 
        default="Olá {nome}, seu trabalho de {curso} ficou pronto com excelência! 🎉\nO valor acordado foi R$ {valor}.\n\nSegue a minha chave PIX para liberação do arquivo: [SUA CHAVE AQUI]"
    )

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# =========================================================
# PROMPT BASE REFINADO (LIMITES EXATOS E TÍTULOS EM NEGRITO)
# =========================================================
PROMPT_REGRAS_BASE = """
    REGRA DE OURO E OBRIGAÇÕES DE SISTEMA (MANDATÓRIO):
    1. PROIBIDO usar palavras robóticas de IA, resumos no final ou formato JSON.
    2. NUNCA formate o texto inteiro em negrito (**). Use negrito apenas para destacar palavras-chave pontuais e os TÍTULOS obrigatórios da Etapa 5.
    3. ATENÇÃO MÁXIMA: É ESTRITAMENTE PROIBIDO DEIXAR QUALQUER TAG DE FORA. Você DEVE gerar textos para TODAS AS 17 TAGS listadas abaixo, sem exceção.

    ESTRUTURA DE PROFUNDIDADE E LIMITES (MUITO IMPORTANTE):
    - ETAPA 2 (Aspectos e Por quês): Os "Aspectos" DEVEM ser frases CURTAS, RÁPIDAS e DIRETAS (máximo 1 a 2 linhas, identificando o problema). Os "Por quês" devem ser LONGOS, profundos e acadêmicos.
    - ETAPAS 3 e 4 (Conceitos, Análise, Soluções): Textos profundos, acadêmicos, densos e detalhados.
    - ETAPA 5 - MEMORIAL ANALÍTICO (O MAIS IMPORTANTE): O limite RIGOROSO do portal é de 6000 caracteres no total desta etapa. Como IAs tendem a escrever a mais, o seu limite MÁXIMO alvo é de 4200 caracteres somando todas as tags da Etapa 5 para deixar uma margem de segurança.
      * OBRIGATÓRIO: Inicie o texto de cada tag da Etapa 5 exatamente com o seu respectivo título em negrito.
      * Resumo: 1 parágrafo denso (~400 caracteres). Inicie com **Resumo**
      * Contexto: 1 parágrafo bem elaborado (~400 caracteres). Inicie com **Contextualização do desafio**
      * Análise: 1 parágrafo com 2 a 3 conceitos (~600 caracteres). Inicie com **Análise**
      * Propostas de solução: 2 parágrafos diretos e embasados (~900 caracteres no total). Inicie com **Propostas de solução**
      * Conclusão reflexiva: Até 2 parágrafos (~600 caracteres). Inicie com **Conclusão reflexiva**
      * Referências: Formato ABNT. Inicie com **Referências**
      * Autoavaliação: 1 parágrafo reflexivo (~400 caracteres). Inicie com **Autoavaliação**

    GERAÇÃO OBRIGATÓRIA (Copie e preencha todas rigorosamente neste formato):
    [START_ASPECTO_1] [Frase curta e direta identificando o problema, ex: Falta de planejamento financeiro claro.] [END_ASPECTO_1]
    [START_POR_QUE_1] [Resposta Profunda e Longa] [END_POR_QUE_1]
    [START_ASPECTO_2] [Frase curta e direta] [END_ASPECTO_2]
    [START_POR_QUE_2] [Resposta Profunda e Longa] [END_POR_QUE_2]
    [START_ASPECTO_3] [Frase curta e direta] [END_ASPECTO_3]
    [START_POR_QUE_3] [Resposta Profunda e Longa] [END_POR_QUE_3]
    [START_CONCEITOS_TEORICOS] [Resposta Profunda e Longa] [END_CONCEITOS_TEORICOS]
    [START_ANALISE_CONCEITO_1] [Resposta Profunda e Longa] [END_ANALISE_CONCEITO_1]
    [START_ENTENDIMENTO_TEORICO] [Resposta Profunda e Longa] [END_ENTENDIMENTO_TEORICO]
    [START_SOLUCOES_TEORICAS] [Resposta Profunda e Longa] [END_SOLUCOES_TEORICAS]

    [START_RESUMO_MEMORIAL] **Resumo**
    [Escreva o texto aqui...] [END_RESUMO_MEMORIAL]
    [START_CONTEXTO_MEMORIAL] **Contextualização do desafio**
    [Escreva o texto aqui...] [END_CONTEXTO_MEMORIAL]
    [START_ANALISE_MEMORIAL] **Análise**
    [Escreva o texto aqui...] [END_ANALISE_MEMORIAL]
    [START_PROPOSTAS_MEMORIAL] **Propostas de solução**
    [Escreva o texto aqui...] [END_PROPOSTAS_MEMORIAL]
    [START_CONCLUSAO_MEMORIAL] **Conclusão reflexiva**
    [Escreva o texto aqui...] [END_CONCLUSAO_MEMORIAL]
    [START_REFERENCIAS_ADICIONAIS] **Referências**
    [Escreva o texto aqui...] [END_REFERENCIAS_ADICIONAIS]
    [START_AUTOAVALIACAO_MEMORIAL] **Autoavaliação**
    [Escreva o texto aqui...] [END_AUTOAVALIACAO_MEMORIAL]
"""

# =========================================================
# INICIALIZAÇÃO BLINDADA E ATUALIZAÇÃO FORÇADA DE BANCO
# =========================================================
with app.app_context():
    db.create_all()
    
    try: 
        db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN status VARCHAR(20) DEFAULT 'Produção'"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN valor FLOAT DEFAULT 70.0"))
        db.session.commit()
    except Exception: 
        db.session.rollback()

    try:
        if not User.query.filter_by(username='admin').first():
            senha_hash = generate_password_hash('admin123')
            admin_user = User(username='admin', password=senha_hash, role='admin')
            db.session.add(admin_user)
            db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try:
        prompt_padrao = PromptConfig.query.filter_by(is_default=True).first()
        if not prompt_padrao:
            novo_prompt = PromptConfig(nome="Padrão Oficial (Desafio UNIASSELVI)", texto=PROMPT_REGRAS_BASE, is_default=True)
            db.session.add(novo_prompt)
            db.session.commit()
        elif "4200 caracteres" not in prompt_padrao.texto:
            # Força a atualização do banco se o prompt antigo não tiver a nova regra de margem de segurança (4200)
            prompt_padrao.texto = PROMPT_REGRAS_BASE
            db.session.commit()
    except Exception: 
        db.session.rollback()

    try:
        if not SiteSettings.query.first():
            db.session.add(SiteSettings())
            db.session.commit()
    except Exception: 
        db.session.rollback()

# =========================================================
# MOTOR NATIVO OPENROUTER (COM LIMITES DE TOKENS EXPANDIDOS)
# =========================================================
def limpar_texto_ia(texto):
    try: 
        texto = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), texto)
    except Exception: 
        pass
    return texto

def chamar_ia(prompt, nome_modelo):
    is_openrouter = "openrouter/" in nome_modelo.lower() or "/" in nome_modelo
    
    if is_openrouter:
        if not CHAVE_OPENROUTER: 
            raise Exception("A Chave da API do OpenRouter não foi configurada.")
            
        modelo_limpo = nome_modelo.replace("openrouter/", "")
            
        headers = {
            "Authorization": f"Bearer {CHAVE_OPENROUTER}",
            "HTTP-Referer": "https://hubmaster-system.com",
            "X-Title": "HubMaster Premium SaaS",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": modelo_limpo,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 8000 # Expandido para 8000. Dá fôlego gigante para não cortar a IA a meio.
        }
        
        try:
            resposta = requests.post(
                "https://openrouter.ai/api/v1/chat/completions", 
                headers=headers, 
                json=payload, 
                timeout=120
            )
            
            if resposta.status_code != 200:
                erro_txt = resposta.text
                try: 
                    erro_txt = resposta.json().get('error', {}).get('message', resposta.text)
                except Exception: 
                    pass
                raise Exception(f"Erro OpenRouter ({resposta.status_code}): {erro_txt}")
                
            dados_ia = resposta.json()
            if 'choices' not in dados_ia or len(dados_ia['choices']) == 0:
                raise Exception("A IA do OpenRouter retornou uma resposta vazia.")
                
            texto_retornado = dados_ia['choices'][0]['message']['content']
            return limpar_texto_ia(texto_retornado)
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Falha de rede ao contactar o OpenRouter: {str(e)}")
            
    else:
        # Google Gemini Nativo
        if not client: 
            raise Exception("A Chave da API nativa do Google não foi configurada.")
            
        try:
            resposta = client.models.generate_content(
                model=nome_modelo, 
                contents=prompt
            )
        except Exception as e:
            raise Exception(f"Erro no Gemini Nativo: {str(e)}")
            
        return limpar_texto_ia(resposta.text)

# =========================================================
# PROCESSAMENTO DE WORD
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

def extrair_texto_docx(arquivo_bytes):
    doc = Document(arquivo_bytes)
    return "\n".join([p.text for p in doc.paragraphs])

def extrair_dicionario(texto_ia):
    chaves = [
        "ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3", 
        "CONCEITOS_TEORICOS", "ANALISE_CONCEITO_1", "ENTENDIMENTO_TEORICO", "SOLUCOES_TEORICAS", 
        "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL", 
        "CONCLUSAO_MEMORIAL", "REFERENCIAS_ADICIONAIS", "AUTOAVALIACAO_MEMORIAL"
    ]
    dic = {}
    
    for chave in chaves:
        padrao = rf"\[START_{chave}\](.*?)(?=\[END_{chave}\]|\[START_|$)"
        match = re.search(padrao, texto_ia, re.DOTALL | re.IGNORECASE)
        
        if match:
            trecho = match.group(1).strip()
            while trecho.startswith('**') and trecho.endswith('**') and len(trecho) > 4:
                trecho = trecho[2:-2].strip()
            dic[f"{{{{{chave}}}}}"] = trecho
        else:
            dic[f"{{{{{chave}}}}}"] = "" 
            
    return dic

def gerar_correcao_ia_tags(texto_tema, texto_trabalho, critica, nome_modelo):
    config = PromptConfig.query.filter_by(is_default=True).first()
    regras = config.texto if config else PROMPT_REGRAS_BASE
    
    prompt = f"""Você é um professor avaliador extremamente rigoroso.
    TEMA: {texto_tema}
    TRABALHO ATUAL: {texto_trabalho}
    CRÍTICA RECEBIDA: {critica}
    TAREFA: Reescreva as respostas aplicando as melhorias exigidas na crítica. 
    NUNCA formate a resposta inteira em negrito.
    {regras}"""
    
    try:
        texto_resposta = chamar_ia(prompt, nome_modelo)
        return extrair_dicionario(texto_resposta)
    except Exception as e: 
        raise Exception(f"Falha na IA (Correção): {str(e)}")

# =========================================================
# ROTAS PÚBLICAS (PORTAL DO ALUNO)
# =========================================================
@app.route('/portal', methods=['GET', 'POST'])
def portal():
    aluno = None
    erro = None
    
    if request.method == 'POST':
        telefone_busca = request.form.get('telefone')
        tel_limpo = re.sub(r'\D', '', telefone_busca)
        
        todos_alunos = Aluno.query.all()
        for a in todos_alunos:
            if re.sub(r'\D', '', a.telefone) == tel_limpo:
                aluno = a
                break
                
        if not aluno: 
            erro = "Nenhum trabalho encontrado para este número de WhatsApp."
            
    return render_template('portal.html', aluno=aluno, erro=erro)

# =========================================================
# ROTAS DO GERADOR E DE REGERAÇÃO
# =========================================================
MODELOS_DISPONIVEIS = [
    "gemini-2.5-flash",                       
    "google/gemini-2.5-flash",                
    "meta-llama/llama-3.3-70b-instruct",      
    "qwen/qwen-2.5-72b-instruct",             
    "mistralai/mistral-nemo"                  
]

@app.route('/')
@login_required
def index():
    if current_user.role == 'cliente' and current_user.expiration_date and date.today() > current_user.expiration_date: 
        return render_template('expirado.html')
        
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.id.desc()).all()
    alunos_ativos = [a for a in todos_alunos if a.status != 'Pago']
    prompts = PromptConfig.query.all()
    
    return render_template('index.html', modelos=MODELOS_DISPONIVEIS, alunos=alunos_ativos, prompts=prompts)

@app.route('/gerar_rascunho', methods=['POST'])
@login_required
def gerar_rascunho():
    tema = request.form.get('tema')
    modelo_selecionado = request.form.get('modelo')
    prompt_id = request.form.get('prompt_id')
    
    config = None
    if prompt_id and str(prompt_id).isdigit():
        config = PromptConfig.query.get(int(prompt_id))
        
    if not config:
        config = PromptConfig.query.filter_by(is_default=True).first()
        
    texto_prompt = config.texto if config else PROMPT_REGRAS_BASE
    prompt_completo = f"TEMA:\n{tema}\n\n{texto_prompt}"
    
    fila_modelos = [modelo_selecionado] + [m for m in MODELOS_DISPONIVEIS if m != modelo_selecionado]
    ultimo_erro = ""

    for modelo in fila_modelos:
        try:
            texto_resposta = chamar_ia(prompt_completo, modelo)
            dicionario = extrair_dicionario(texto_resposta)
            
            tags_preenchidas = sum(1 for v in dicionario.values() if v.strip())
            if tags_preenchidas < 10: 
                raise Exception(f"A IA {modelo} teve preguiça e gerou apenas {tags_preenchidas} tags. O sistema tentará a IA de reserva.")
                
            novo_registro = RegistroUso(modelo_usado=modelo)
            db.session.add(novo_registro)
            db.session.commit()
            
            return jsonify({"sucesso": True, "dicionario": dicionario, "modelo_utilizado": modelo})
            
        except Exception as e:
            ultimo_erro = str(e)
            continue
            
    return jsonify({
        "sucesso": False, 
        "erro": f"Erro Fatal. Todas as IAs do sistema reportaram falha técnica ou preguiça extrema. Último erro: {ultimo_erro}", 
        "fallback": False
    })

@app.route('/regerar_trecho', methods=['POST'])
@login_required
def regerar_trecho():
    try:
        dados = request.json
        if not dados: 
            return jsonify({"sucesso": False, "erro": "Nenhum dado recebido."})

        tema = dados.get('tema', '')
        tag = dados.get('tag', '')
        modelo_selecionado = dados.get('modelo', MODELOS_DISPONIVEIS[0])
        prompt_id = dados.get('prompt_id')
        contexto_atual = dados.get('dicionario', {}) 

        config = None
        if prompt_id and str(prompt_id).isdigit():
            config = PromptConfig.query.get(int(prompt_id))
            
        if not config:
            config = PromptConfig.query.filter_by(is_default=True).first()
            
        texto_prompt = config.texto if config else PROMPT_REGRAS_BASE

        texto_contexto = ""
        for chave, valor in contexto_atual.items():
            if valor and str(valor).strip(): 
                texto_contexto += f"{chave}:\n{valor}\n\n"

        prompt_regeracao = f"""Você é um professor avaliador rigoroso.
TEMA/CASO DO DESAFIO:\n{tema}

CONTEXTO ATUAL DO TRABALHO (Para manter a coerência):\n{texto_contexto}

REGRAS GERAIS E ESTRUTURA:\n{texto_prompt}

TAREFA ESPECÍFICA DE CORREÇÃO:
Reescreva APENAS o trecho da tag {tag}. É OBRIGATÓRIO que faça sentido com o contexto. NÃO inclua as marcações [START_{tag}] ou [END_{tag}]. NUNCA formate a resposta toda em negrito (**). Retorne APENAS o texto limpo."""
        
        fila_modelos = [modelo_selecionado] + [m for m in MODELOS_DISPONIVEIS if m != modelo_selecionado]
        ultimo_erro = ""

        for modelo in fila_modelos:
            try:
                novo_texto = chamar_ia(prompt_regeracao, modelo)
                novo_texto = re.sub(rf"\[START_{tag}\]", "", novo_texto, flags=re.IGNORECASE)
                novo_texto = re.sub(rf"\[END_{tag}\]", "", novo_texto, flags=re.IGNORECASE).strip()
                
                while novo_texto.startswith('**') and novo_texto.endswith('**') and len(novo_texto) > 4: 
                    novo_texto = novo_texto[2:-2].strip()
                    
                return jsonify({"sucesso": True, "novo_texto": novo_texto, "modelo_utilizado": modelo})
                
            except Exception as e:
                ultimo_erro = str(e)
                continue
                
        return jsonify({"sucesso": False, "erro": f"Todas as IAs falharam ao regerar o trecho. Erro: {ultimo_erro}"})
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/gerar_docx_final', methods=['POST'])
@login_required
def gerar_docx_final():
    try:
        dados = request.json or {}
        aluno_id = dados.get('aluno_id')
        dicionario_editado = dados.get('dicionario', {})
        
        caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
        with open(caminho_padrao, 'rb') as f: 
            arquivo_memoria = io.BytesIO(f.read())
            
        documento_pronto = preencher_template_com_tags(arquivo_memoria, dicionario_editado)
        arquivo_bytes = documento_pronto.read()
        
        if aluno_id:
            novo_doc = Documento(
                aluno_id=aluno_id, 
                nome_arquivo=f"Trabalho_{datetime.now().strftime('%d%m%Y')}.docx", 
                dados_arquivo=arquivo_bytes
            )
            db.session.add(novo_doc)
            
            aluno = Aluno.query.get(aluno_id)
            if aluno and (aluno.status == 'Produção' or aluno.status is None): 
                aluno.status = 'Pendente'
                
            db.session.commit()
            
        return jsonify({
            "sucesso": True, 
            "arquivo_base64": base64.b64encode(arquivo_bytes).decode('utf-8')
        })
        
    except Exception as e: 
        return jsonify({"sucesso": False, "erro": str(e)})

# =========================================================
# DASHBOARD E CONFIGURAÇÕES
# =========================================================
@app.route('/dashboard')
@login_required
def dashboard():
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).all()
    
    a_receber = sum((a.valor or 70.0) for a in todos_alunos if a.status != 'Pago')
    receita_realizada = sum((a.valor or 70.0) for a in todos_alunos if a.status == 'Pago')
    
    hoje = datetime.utcnow()
    labels_meses = []
    
    for i in range(5, -1, -1):
        m = hoje.month - i
        y = hoje.year
        if m <= 0:
            m += 12
            y -= 1
        labels_meses.append((y, m))
        
    faturamento_dict = {(y, m): 0.0 for y, m in labels_meses}
    pedidos_dias = [0] * 7
    
    for a in todos_alunos:
        if a.data_cadastro:
            pedidos_dias[a.data_cadastro.weekday()] += 1
            if a.status == 'Pago':
                chave = (a.data_cadastro.year, a.data_cadastro.month)
                if chave in faturamento_dict: 
                    faturamento_dict[chave] += (a.valor or 70.0)
                    
    uso_modelos = db.session.query(RegistroUso.modelo_usado, db.func.count(RegistroUso.id)).group_by(RegistroUso.modelo_usado).all()
    meses_nomes = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    
    grafico_meses_labels = [f"{meses_nomes[m-1]}/{str(y)[2:]}" for y, m in labels_meses]
    grafico_meses_valores = [faturamento_dict[k] for k in labels_meses]
    
    return render_template(
        'dashboard.html', 
        a_receber=a_receber, 
        receita_realizada=receita_realizada, 
        total_trabalhos=len(todos_alunos), 
        uso_modelos=uso_modelos, 
        graf_meses_lbl=grafico_meses_labels, 
        graf_meses_val=grafico_meses_valores, 
        graf_dias_lbl=['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'], 
        graf_dias_val=pedidos_dias
    )

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    config = SiteSettings.query.first()
    
    if request.method == 'POST':
        config.whatsapp_template = request.form.get('whatsapp_template')
        db.session.commit()
        flash('Configurações salvas com sucesso!', 'success')
        return redirect(url_for('configuracoes'))
        
    return render_template('configuracoes.html', config=config)

@app.route('/prompts', methods=['GET', 'POST'])
@login_required
def gerenciar_prompts():
    if request.method == 'POST':
        novo_prompt = PromptConfig(
            nome=request.form.get('nome'), 
            texto=request.form.get('texto')
        )
        db.session.add(novo_prompt)
        db.session.commit()
        flash('Novo Cérebro de IA adicionado!', 'success')
        return redirect(url_for('gerenciar_prompts'))
        
    return render_template('prompts.html', prompts=PromptConfig.query.all())

@app.route('/prompts/delete/<int:id>')
@login_required
def delete_prompt(id):
    p = PromptConfig.query.get_or_404(id)
    if not p.is_default: 
        db.session.delete(p)
        db.session.commit()
        
    return redirect(url_for('gerenciar_prompts'))

# =========================================================
# CRM E GESTÃO DE CLIENTES
# =========================================================
@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    if request.method == 'POST':
        novo_aluno = Aluno(
            user_id=current_user.id, 
            nome=request.form.get('nome'), 
            curso=request.form.get('curso'), 
            telefone=request.form.get('telefone'), 
            valor=float(request.form.get('valor', 70.0)), 
            status='Pendente'
        )
        db.session.add(novo_aluno)
        db.session.commit()
        flash('Cliente cadastrado!', 'success')
        return redirect(url_for('clientes'))
    
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.id.desc()).all()
    alunos_pendentes = [a for a in todos_alunos if a.status != 'Pago']
    alunos_pagos = [a for a in todos_alunos if a.status == 'Pago']
    
    return render_template(
        'clientes.html', 
        alunos_pendentes=alunos_pendentes, 
        alunos_pagos=alunos_pagos, 
        config=SiteSettings.query.first()
    )

@app.route('/editar_valor/<int:id>', methods=['POST'])
@login_required
def editar_valor(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    try: 
        aluno.valor = float(request.form.get('novo_valor').replace(',', '.'))
        db.session.commit()
        flash(f'Valor atualizado!', 'success')
    except Exception: 
        flash('Valor inválido.', 'error')
        
    return redirect(url_for('clientes'))

@app.route('/editar_cliente/<int:id>', methods=['POST'])
@login_required
def editar_cliente(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    aluno.nome = request.form.get('nome')
    aluno.curso = request.form.get('curso')
    aluno.telefone = request.form.get('telefone')
    
    try: 
        aluno.valor = float(request.form.get('valor').replace(',', '.'))
    except Exception: 
        pass
        
    db.session.commit()
    flash(f'Dados atualizados!', 'success')
    return redirect(url_for('clientes'))

@app.route('/deletar_cliente/<int:id>', methods=['GET'])
@login_required
def deletar_cliente(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    db.session.delete(aluno)
    db.session.commit()
    flash(f'Cliente apagado.', 'success')
    return redirect(url_for('clientes'))

@app.route('/toggle_status/<int:id>', methods=['POST'])
@login_required
def toggle_status(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    aluno.status = 'Pago' if aluno.status != 'Pago' else 'Pendente'
    db.session.commit()
    return redirect(url_for('clientes'))

@app.route('/cliente/<int:id>', methods=['GET'])
@login_required
def cliente_detalhe(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    return render_template('cliente_detalhe.html', aluno=aluno)

@app.route('/adicionar_tema/<int:aluno_id>', methods=['POST'])
@login_required
def adicionar_tema(aluno_id):
    aluno = Aluno.query.get_or_404(aluno_id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    titulo = request.form.get('titulo')
    texto = request.form.get('texto')
    
    if texto: 
        novo_tema = TemaTrabalho(aluno_id=aluno.id, titulo=titulo if titulo else f"Tema {len(aluno.temas) + 1}", texto=texto)
        db.session.add(novo_tema)
        db.session.commit()
        flash('Tema salvo!', 'success')
    else: 
        flash('O texto não pode estar vazio.', 'error')
        
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/deletar_tema/<int:tema_id>', methods=['GET'])
@login_required
def deletar_tema(tema_id):
    tema = TemaTrabalho.query.get_or_404(tema_id)
    aluno = Aluno.query.get(tema.aluno_id)
    
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    db.session.delete(tema)
    db.session.commit()
    flash('Tema apagado.', 'success')
    return redirect(url_for('cliente_detalhe', id=tema.aluno_id))

@app.route('/upload_doc/<int:aluno_id>', methods=['POST'])
@login_required
def upload_doc(aluno_id):
    arquivo = request.files['arquivo']
    if arquivo:
        if arquivo.filename.lower().endswith(('.docx', '.pdf')): 
            novo_doc = Documento(aluno_id=aluno_id, nome_arquivo=arquivo.filename, dados_arquivo=arquivo.read())
            db.session.add(novo_doc)
            db.session.commit()
            flash('Anexado com sucesso!', 'success')
        else: 
            flash('Apenas .docx e .pdf.', 'error')
            
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/download_doc/<int:doc_id>')
def download_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    return send_file(io.BytesIO(doc.dados_arquivo), download_name=doc.nome_arquivo, as_attachment=True)

@app.route('/rename_doc/<int:doc_id>', methods=['POST'])
@login_required
def rename_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    novo_nome = request.form.get('novo_nome')
    extensao = os.path.splitext(doc.nome_arquivo)[1] 
    
    if not novo_nome.lower().endswith(extensao.lower()): 
        novo_nome += extensao
        
    doc.nome_arquivo = novo_nome
    db.session.commit()
    return redirect(url_for('cliente_detalhe', id=doc.aluno_id))

@app.route('/delete_doc/<int:doc_id>')
@login_required
def delete_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    aluno_id = doc.aluno_id
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for('cliente_detalhe', id=aluno_id))

# =========================================================
# REVISÃO AVULSA
# =========================================================
@app.route('/revisao_avulsa')
@login_required
def revisao_avulsa(): 
    return render_template('revisao_avulsa.html', modelos=MODELOS_DISPONIVEIS)

@app.route('/avaliar_avulso', methods=['POST'])
@login_required
def avaliar_avulso():
    tema = request.form.get('tema')
    modelo = request.form.get('modelo')
    arquivo = request.files.get('arquivo_trabalho')
    
    if not arquivo or not arquivo.filename.endswith('.docx'): 
        return jsonify({"erro": "Envie um arquivo .docx válido."}), 400
        
    texto_trabalho = extrair_texto_docx(arquivo)
    prompt = f"""Você é um professor avaliador extremamente rigoroso.
Analise o TEMA: {tema} \nE O TRABALHO DO ALUNO: {texto_trabalho}
Faça uma crítica de 3 linhas apontando o que falta para tirar nota máxima. 
REGRA: Seja direto, NUNCA use formatações como negrito (**), itálico, bullet points ou títulos. Responda apenas com texto limpo."""
    
    try: 
        resposta_ia = chamar_ia(prompt, modelo)
        critica_limpa = resposta_ia.replace('*', '').replace('#', '').strip()
        return jsonify({
            "critica": critica_limpa, 
            "texto_extraido": texto_trabalho
        })
    except Exception as e: 
        return jsonify({"erro": str(e)}), 500

@app.route('/corrigir_avulso', methods=['POST'])
@login_required
def corrigir_avulso():
    tema = request.form.get('tema')
    texto_trabalho = request.form.get('texto_extraido')
    critica = request.form.get('critica')
    modelo = request.form.get('modelo')
    
    try:
        respostas = gerar_correcao_ia_tags(tema, texto_trabalho, critica, modelo)
        
        caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
        with open(caminho_padrao, 'rb') as f: 
            arquivo_memoria = io.BytesIO(f.read())
            
        documento_pronto = preencher_template_com_tags(arquivo_memoria, respostas)
        arquivo_base64 = base64.b64encode(documento_pronto.read()).decode('utf-8')
        
        return jsonify({
            "arquivo_base64": arquivo_base64, 
            "nome_arquivo": "Trabalho_Revisado_IA.docx"
        })
    except Exception as e: 
        return jsonify({"erro": str(e)}), 500

# =========================================================
# LOGIN E ADMINISTRAÇÃO
# =========================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: 
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        else: 
            flash('Credenciais incorretas.', 'error')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): 
    logout_user()
    return redirect(url_for('login'))

@app.route('/mudar_senha', methods=['GET', 'POST'])
@login_required
def mudar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        
        if check_password_hash(current_user.password, senha_atual):
            current_user.password = generate_password_hash(nova_senha)
            db.session.commit()
            flash('Sua senha foi atualizada!', 'success')
            return redirect(url_for('index'))
            
    return render_template('mudar_senha.html')

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    users = User.query.all()
    return render_template('admin.html', users=users, hoje=date.today())

@app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_user(id):
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    user = User.query.get_or_404(id)
    return render_template('edit_user.html', user=user)

@app.route('/delete_user/<int:id>')
@login_required
def delete_user(id):
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    user = User.query.get_or_404(id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True)
