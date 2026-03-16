import os
import io
import re
import base64
import json
import requests
import threading
import logging
import traceback
import hashlib
import csv
import zipfile  
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, abort, send_file, Response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import documentos
import ia_core

app = Flask(__name__)

# =========================================================
# BLINDAGEM DA EXTENSÃO FANTASMA (Sessão Cruzada)
# =========================================================
CORS(app, supports_credentials=True)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-super-secreta-mude-depois')

# Permitir que a Extensão leia o login do usuário estando na Uniasselvi
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True

# =========================================================
# SISTEMA DE LOGS E TRATAMENTO DE ERROS GLOBAL
# =========================================================
logging.basicConfig(filename='sistema_erros.log', level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

@app.errorhandler(Exception)
def handle_exception(e):
    logging.error("Falha Interna Detectada:", exc_info=True)
    try: 
        db.session.rollback()
    except Exception: 
        pass
    
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): 
        return e
    
    # Se o erro for requisitado pela extensão, devolve JSON puro
    if request.path.startswith('/api/'):
        return jsonify({"sucesso": False, "erro": f"Erro interno do Servidor Koyeb: {str(e)}"}), 500
        
    return f"""
    <div style="font-family: sans-serif; padding: 20px; background: #262730; color: #E1E4E8; height: 100vh;">
        <h1 style="color: #FF4B4B;">🚨 Ops! Houve um problema.</h1>
        <p>A nossa equipa técnica já foi notificada silenciosamente através do sistema de logs.</p>
        <div style="background: #1A1C23; padding: 15px; border-radius: 5px; color: #FFC107; font-family: monospace; overflow-x: auto;">
            <b>{type(e).__name__}</b>: {str(e)}
        </div>
        <p style="margin-top: 20px;">
            <a href="/" style="color: #2196F3; text-decoration: none;">⬅️ Tentar Voltar à Página Inicial</a>
        </p>
    </div>
    """, 500

# =========================================================
# CONFIGURAÇÕES DO BANCO DE DADOS
# =========================================================
db_url = os.environ.get('DATABASE_URL', 'sqlite:///clientes.db')
if db_url.startswith("postgres://"): 
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if db_url.startswith("postgresql"):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_pre_ping": True, 
        "pool_recycle": 300, 
        "pool_timeout": 30,
        "pool_size": 20,
        "max_overflow": 30
    }
else:
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
    creditos = db.Column(db.Integer, default=0)
    alunos = db.relationship('Aluno', backref='responsavel', lazy=True)
    gabaritos = db.relationship('GabaritoSalvo', backref='dono', lazy=True) 

class GabaritoSalvo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    prova_texto = db.Column(db.Text, nullable=False)
    resultado_json = db.Column(db.Text, nullable=False)
    hash_prova = db.Column(db.String(255), nullable=True)
    data_geracao = db.Column(db.DateTime, default=datetime.utcnow)

class Aluno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    curso = db.Column(db.String(100))
    telefone = db.Column(db.String(20))
    ava_login = db.Column(db.String(255), nullable=True) 
    ava_senha = db.Column(db.String(255), nullable=True) 
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    data_pagamento = db.Column(db.DateTime, nullable=True) 
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
    custo = db.Column(db.Float, default=0.0)

class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    whatsapp_template = db.Column(
        db.Text, 
        default="Olá {nome}, seu trabalho de {curso} ficou pronto com excelência! 🎉\nO valor acordado foi R$ {valor}.\n\nSegue a minha chave PIX para liberação do arquivo: [SUA CHAVE AQUI]"
    )
    prompt_password = db.Column(db.String(255), nullable=True)
    convert_api_key = db.Column(db.String(255), nullable=True)
    modelos_ativos = db.Column(db.Text, nullable=True)

class GeracaoTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='Pendente') 
    resultado = db.Column(db.Text, nullable=True) 
    modelo_utilizado = db.Column(db.String(100), nullable=True)
    erro = db.Column(db.Text, nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# =========================================================
# PROMPT BASE DE ELITE
# =========================================================
PROMPT_REGRAS_BASE = """VOCÊ AGORA ASSUME A PERSONA DE UM PROFESSOR UNIVERSITÁRIO AVALIADOR EXTREMAMENTE RIGOROSO E DE ALTA EXCELÊNCIA ACADÊMICA.

REGRAS DE VOCABULÁRIO (ESTRITAMENTE PROIBIDO):
É proibido usar as seguintes palavras ou expressões: momentum, locus, outrossim, dessarte, destarte, mergulho profundo, testamento, tapeçaria, farol, crucial, vital, paisagem, adentrar, notável, multifacetada, teia.
Não use formatação em itálico (asterisco simples) para termos em inglês como soft skills ou hard skills."""

# =========================================================
# INICIALIZAÇÃO E MIGRAÇÕES
# =========================================================
with app.app_context():
    db.create_all()
    try: 
        db.session.execute(db.text('ALTER TABLE "user" ADD COLUMN creditos INTEGER DEFAULT 0'))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE gabarito_salvo ADD COLUMN hash_prova VARCHAR(255)"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
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
        db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN ava_login VARCHAR(255)"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN ava_senha VARCHAR(255)"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE site_settings ADD COLUMN prompt_password VARCHAR(255)"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE site_settings ADD COLUMN convert_api_key VARCHAR(255)"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE registro_uso ADD COLUMN custo FLOAT DEFAULT 0.0"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN data_pagamento TIMESTAMP"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE site_settings ADD COLUMN modelos_ativos TEXT"))
        db.session.commit()
    except Exception: 
        db.session.rollback()

    # Atualizar datas de pagamento vazias
    try:
        alunos_pagos = Aluno.query.filter_by(status='Pago').all()
        for al in alunos_pagos:
            if not al.data_pagamento:
                al.data_pagamento = al.data_cadastro
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Criar dados padrão se estiver vazio
    try:
        if not User.query.filter_by(username='admin').first():
            senha_hash = generate_password_hash('admin123')
            admin_user = User(username='admin', password=senha_hash, role='admin', creditos=0)
            db.session.add(admin_user)
            db.session.commit()
            
        if not PromptConfig.query.filter_by(is_default=True).first():
            novo_prompt = PromptConfig(nome="Padrão Oficial (Desafio UNIASSELVI)", texto=PROMPT_REGRAS_BASE, is_default=True)
            db.session.add(novo_prompt)
            db.session.commit()
            
        if not SiteSettings.query.first():
            db.session.add(SiteSettings())
            db.session.commit()
    except Exception: 
        db.session.rollback()

# =========================================================
# GESTÃO DE MODELOS E BACKGROUND TASKS
# =========================================================
TODOS_MODELOS_CONHECIDOS = [
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-opus",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "qwen/qwen-2.5-72b-instruct",
    "mistralai/mistral-large",
    "x-ai/grok-2-vision"
]

def get_modelos_ativos():
    config = SiteSettings.query.first()
    if config and config.modelos_ativos:
        return [m.strip() for m in config.modelos_ativos.split(',') if m.strip()]
    return ["anthropic/claude-3.5-sonnet", "google/gemini-2.5-pro", "google/gemini-2.5-flash", "meta-llama/llama-3.3-70b-instruct", "qwen/qwen-2.5-72b-instruct"]

def executar_geracao_bg(task_id, prompt_completo, fila_modelos):
    with app.app_context():
        ultimo_erro = ""
        modelos_para_tentar = fila_modelos[:2]
        
        for modelo in modelos_para_tentar:
            try:
                texto_resposta, custo_estimado = ia_core.chamar_ia(prompt_completo, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
                dicionario = ia_core.extrair_dicionario(texto_resposta)
                
                tags_preenchidas = sum(1 for v in dicionario.values() if v.strip())
                
                if tags_preenchidas < 3: 
                    raise Exception(f"A IA {modelo} falhou severamente ao preencher as tags.")
                
                task_verificar = GeracaoTask.query.get(task_id)
                if task_verificar and task_verificar.status == 'Cancelado':
                    db.session.rollback()
                    return 
                    
                novo_registro = RegistroUso(modelo_usado=modelo, custo=custo_estimado)
                db.session.add(novo_registro)
                
                task_verificar.status = 'Concluido'
                task_verificar.resultado = json.dumps(dicionario)
                task_verificar.modelo_utilizado = modelo
                db.session.commit()
                return
                
            except Exception as e:
                ultimo_erro = str(e)
                continue
                
        task_erro = GeracaoTask.query.get(task_id)
        if task_erro and task_erro.status != 'Cancelado':
            task_erro.status = 'Erro'
            task_erro.erro = f"Falha ao processar as IAs. Último erro: {ultimo_erro}"
            db.session.commit()

# =========================================================
# ROTAS PÚBLICAS E AUTENTICAÇÃO
# =========================================================
@app.route('/')
@login_required
def index():
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.id.desc()).all()
    alunos_ativos = [a for a in todos_alunos if a.status == 'Produção' or not a.status]
    prompts = PromptConfig.query.all()
    return render_template('index.html', modelos=get_modelos_ativos(), alunos=alunos_ativos, prompts=prompts)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: 
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
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
        if check_password_hash(current_user.password, request.form.get('senha_atual')):
            current_user.password = generate_password_hash(request.form.get('nova_senha'))
            db.session.commit()
            flash('Sua senha foi atualizada!', 'success')
            return redirect(url_for('index'))
        else:
            flash('A senha atual está incorreta.', 'error')
            
    return render_template('mudar_senha.html')

# =========================================================
# GABARITO INTELIGENTE & MENTE COLETIVA
# =========================================================
@app.route('/gabarito_inteligente')
@login_required
def gabarito_inteligente():
    return render_template('gabarito_inteligente.html')

@app.route('/api/gerar_gabarito', methods=['POST', 'OPTIONS'])
def api_gerar_gabarito():
    if request.method == 'OPTIONS':
        return jsonify({"sucesso": True}), 200

    # 🔒 A BLINDAGEM MÁXIMA DA EXTENSÃO: Bloqueia quem não estiver logado no HubMaster
    if not current_user.is_authenticated:
        return jsonify({"sucesso": False, "erro": "auth_required"}), 401

    texto_prova = request.form.get('prova', '')
    if not texto_prova: 
        return jsonify({"sucesso": False, "erro": "O texto da prova está vazio."})

    texto_limpo = re.sub(r'[\W_]+', '', texto_prova.lower())
    hash_prova_atual = hashlib.md5(texto_limpo.encode('utf-8')).hexdigest()

    try:
        gabarito_em_cache = GabaritoSalvo.query.filter_by(hash_prova=hash_prova_atual).first()
        if gabarito_em_cache:
            respostas_salvas = json.loads(gabarito_em_cache.resultado_json)
            return jsonify({
                "sucesso": True, 
                "gabarito": respostas_salvas, 
                "modelo_utilizado": "🧠 Mente Coletiva (Zero Custo)"
            })
    except Exception as e:
        db.session.rollback()

    modelo_elite = "anthropic/claude-3.5-sonnet"
    prompt = f"Resolva a prova abaixo. Retorne EXATAMENTE um Array JSON puro.\nEstrutura: [{{\"questao\": 1, \"resposta\": \"A\", \"justificativa\": \"Motivo.\"}}]\nPROVA:\n{texto_prova}"
    
    try:
        resposta_ia, custo = ia_core.chamar_ia(prompt, modelo_elite, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        resultado_ia = ia_core.extrair_json_seguro(resposta_ia)
    except Exception as e:
        return jsonify({"sucesso": False, "erro": f"Falha na IA: {e}"})

    if not resultado_ia: 
        return jsonify({"sucesso": False, "erro": "A IA falhou ao processar o gabarito."})

    gabarito_final = []
    for idx, q in enumerate(resultado_ia):
        letra_crua = str(q.get('resposta', '')).upper().strip()
        match_letra = re.search(r'[A-E]', letra_crua)
        gabarito_final.append({
            "questao": q.get('questao', idx + 1),
            "resposta": match_letra.group(0) if match_letra else "?",
            "justificativa": q.get('justificativa', 'Sem justificativa.')
        })

    try:
        # Grava a prova exatamente no nome do usuário atual
        novo_registro = RegistroUso(modelo_usado=modelo_elite, custo=custo)
        novo_gabarito = GabaritoSalvo(
            user_id=current_user.id, 
            prova_texto=texto_prova, 
            resultado_json=json.dumps(gabarito_final), 
            hash_prova=hash_prova_atual
        )
        
        db.session.add(novo_registro)
        db.session.add(novo_gabarito)
        db.session.commit()
    except Exception as e:
        db.session.rollback()

    return jsonify({"sucesso": True, "gabarito": gabarito_final, "modelo_utilizado": modelo_elite})

@app.route('/banco_gabaritos')
@login_required
def banco_gabaritos():
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    gabaritos_salvos = GabaritoSalvo.query.order_by(GabaritoSalvo.id.desc()).all()
    
    for g in gabaritos_salvos:
        try: 
            g.respostas_parsed = json.loads(g.resultado_json)
        except Exception: 
            g.respostas_parsed = []
        
    return render_template('banco_gabaritos.html', gabaritos=gabaritos_salvos)

@app.route('/corrigir_gabarito/<int:id>', methods=['POST'])
@login_required
def corrigir_gabarito(id):
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    gabarito = GabaritoSalvo.query.get_or_404(id)
    dados = request.json
    
    questao_num = int(dados.get('questao'))
    nova_letra = str(dados.get('nova_letra')).upper().strip()
    
    if nova_letra not in ['A', 'B', 'C', 'D', 'E']:
        return jsonify({"sucesso": False, "erro": "Letra inválida."})

    try:
        respostas = json.loads(gabarito.resultado_json)
        modificado = False
        
        for r in respostas:
            if int(r.get('questao')) == questao_num:
                r['resposta'] = nova_letra
                # Coloca uma tag na justificativa para você lembrar que o humano interveio!
                if not "[CORRIGIDO" in r.get('justificativa', ''):
                    r['justificativa'] = f"🤖⚙️ [CORRIGIDO PELO ADMIN] " + r.get('justificativa', '')
                modificado = True
                break

        if modificado:
            gabarito.resultado_json = json.dumps(respostas)
            db.session.commit()
            return jsonify({"sucesso": True})
        else:
            return jsonify({"sucesso": False, "erro": "Questão não encontrada na prova."})
            
    except Exception as e:
        db.session.rollback()
        return jsonify({"sucesso": False, "erro": str(e)})


# =========================================================
# FERRAMENTA: PROJETOS DE EXTENSÃO
# =========================================================
@app.route('/projetos_extensao')
@login_required
def projetos_extensao():
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.nome).all()
    return render_template('projetos_extensao.html', alunos=todos_alunos)

@app.route('/gerar_extensao', methods=['POST'])
@login_required
def gerar_extensao():
    aluno_id = request.form.get('aluno_id')
    matricula = request.form.get('matricula', 'Não informada')
    nome_avulso = request.form.get('nome_avulso', '')
    curso_avulso = request.form.get('curso_avulso', '')
    gerar_pdf = request.form.get('gerar_pdf') == 'sim'
    
    aluno = Aluno.query.get(aluno_id) if aluno_id else None
    nome_aluno = aluno.nome if aluno else nome_avulso
    curso_aluno = aluno.curso if aluno else curso_avulso

    caminho_evidencias = os.path.join(app.root_path, 'TEMPLATE_EVIDENCIAS_PARANA.docx')
    caminho_ficha = os.path.join(app.root_path, 'TEMPLATE_FICHA_PARANA.docx')
    
    if not os.path.exists(caminho_evidencias) or not os.path.exists(caminho_ficha):
        flash('Erro: Os arquivos TEMPLATE_EVIDENCIAS_PARANA.docx ou TEMPLATE_FICHA_PARANA.docx não foram encontrados no servidor.', 'error')
        return redirect(url_for('projetos_extensao'))

    quantidade_datas = 30 
    try:
        with zipfile.ZipFile(caminho_ficha, 'r') as zf:
            xml_content = zf.read('word/document.xml').decode('utf-8')
            texto_puro = re.sub(r'<[^>]+>', '', xml_content)
            texto_limpo = re.sub(r'[\u200b\u200c\u200d\ufeff\xa0\s]', '', texto_puro).upper()
            numeros = re.findall(r'DATA_?(\d+)', texto_limpo)
            if numeros:
                quantidade_datas = max(int(n) for n in numeros)
    except Exception as e:
        print(f"Erro ao detectar tags: {e}")

    datas_reversas = []
    agora_utc = datetime.utcnow()
    data_atual = (agora_utc - timedelta(hours=3)).date()
    
    datas_reversas.append(data_atual) 
    if quantidade_datas > 1:
        datas_reversas.append(data_atual)
    
    data_temp = data_atual - timedelta(days=1)
    
    while len(datas_reversas) < quantidade_datas:
        if data_temp.weekday() != 6:
            datas_reversas.append(data_temp)
        data_temp -= timedelta(days=1)
        
    datas_reversas.reverse() 

    dicionario = {
        "{{NOME_ALUNO}}": nome_aluno,
        "{{MATRICULA}}": matricula,
        "{{CURSO}}": curso_aluno
    }
    
    for i in range(quantidade_datas):
        dicionario[f"{{{{DATA_{i+1}}}}}"] = datas_reversas[i].strftime('%d/%m/%Y')

    try:
        with open(caminho_evidencias, 'rb') as f1:
            memoria_evidencias = io.BytesIO(f1.read())
        doc_evidencias = documentos.preencher_template_extensao(memoria_evidencias, dicionario)
        bytes_evidencias = doc_evidencias.read()
        
        with open(caminho_ficha, 'rb') as f2:
            memoria_ficha = io.BytesIO(f2.read())
        doc_ficha = documentos.preencher_template_extensao(memoria_ficha, dicionario)
        bytes_ficha = doc_ficha.read()

        nome_arq_evidencias = f"[EXTENSÃO] Evidências - {nome_aluno}.docx"
        nome_arq_ficha = f"[EXTENSÃO] Ficha - {nome_aluno}.docx"

        pdf_evidencias_bytes = None
        pdf_ficha_bytes = None

        if gerar_pdf:
            config = SiteSettings.query.first()
            if config and config.convert_api_key:
                resp1 = requests.post(f'https://v2.convertapi.com/convert/docx/to/pdf?Secret={config.convert_api_key}', files={'File': (nome_arq_evidencias, bytes_evidencias)}).json()
                if 'Files' in resp1: 
                    pdf_evidencias_bytes = base64.b64decode(resp1['Files'][0]['FileData'])
                
                resp2 = requests.post(f'https://v2.convertapi.com/convert/docx/to/pdf?Secret={config.convert_api_key}', files={'File': (nome_arq_ficha, bytes_ficha)}).json()
                if 'Files' in resp2: 
                    pdf_ficha_bytes = base64.b64decode(resp2['Files'][0]['FileData'])
            else:
                flash('Chave da ConvertAPI não encontrada nas Configurações.', 'warning')

        if aluno:
            db.session.add(Documento(aluno_id=aluno.id, nome_arquivo=nome_arq_evidencias, dados_arquivo=bytes_evidencias))
            db.session.add(Documento(aluno_id=aluno.id, nome_arquivo=nome_arq_ficha, dados_arquivo=bytes_ficha))
            
            if pdf_evidencias_bytes:
                db.session.add(Documento(aluno_id=aluno.id, nome_arquivo=nome_arq_evidencias.replace('.docx','.pdf'), dados_arquivo=pdf_evidencias_bytes))
            if pdf_ficha_bytes:
                db.session.add(Documento(aluno_id=aluno.id, nome_arquivo=nome_arq_ficha.replace('.docx','.pdf'), dados_arquivo=pdf_ficha_bytes))
            
            db.session.commit()
            flash('Projeto de Extensão gerado e salvo com sucesso!', 'success')
            return redirect(url_for('cliente_detalhe', id=aluno.id))
        else:
            arquivos_para_baixar = [
                {"nome": nome_arq_evidencias, "b64": base64.b64encode(bytes_evidencias).decode('utf-8')},
                {"nome": nome_arq_ficha, "b64": base64.b64encode(bytes_ficha).decode('utf-8')}
            ]
            
            if pdf_evidencias_bytes:
                arquivos_para_baixar.append({"nome": nome_arq_evidencias.replace('.docx','.pdf'), "b64": base64.b64encode(pdf_evidencias_bytes).decode('utf-8')})
            if pdf_ficha_bytes:
                arquivos_para_baixar.append({"nome": nome_arq_ficha.replace('.docx','.pdf'), "b64": base64.b64encode(pdf_ficha_bytes).decode('utf-8')})
            
            todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.nome).all()
            return render_template('projetos_extensao.html', alunos=todos_alunos, avulsos=arquivos_para_baixar)

    except Exception as e:
        flash(f'Erro ao processar os documentos: {str(e)}', 'error')
        return redirect(url_for('projetos_extensao'))


# =========================================================
# ROTAS DE TRABALHOS ACADÊMICOS E CRM
# =========================================================
@app.route('/api/temas/<int:aluno_id>')
@login_required
def get_temas_aluno(aluno_id):
    temas = TemaTrabalho.query.filter_by(aluno_id=aluno_id).all()
    resultado = [{"id": t.id, "titulo": t.titulo, "texto": t.texto} for t in temas]
    return jsonify(resultado)

@app.route('/api/extrair_memorial/<int:doc_id>', methods=['GET'])
@login_required
def api_extrair_memorial(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    if doc.aluno.user_id != current_user.id and current_user.role != 'admin':
        return jsonify({"sucesso": False, "erro": "Acesso negado."}), 403
        
    try:
        sucesso, texto_memorial = documentos.extrair_etapa_5(doc.dados_arquivo)
        return jsonify({
            "sucesso": sucesso, 
            "texto": texto_memorial if sucesso else "", 
            "erro": texto_memorial if not sucesso else ""
        })
    except Exception as e:
        return jsonify({"sucesso": False, "erro": f"Erro interno: {str(e)}"})

@app.route('/gerar_rascunho', methods=['POST'])
@login_required
def gerar_rascunho():
    tema = request.form.get('tema')
    modelo_selecionado = request.form.get('modelo')
    prompt_id = request.form.get('prompt_id')
    
    config = PromptConfig.query.get(int(prompt_id)) if prompt_id and str(prompt_id).isdigit() else PromptConfig.query.filter_by(is_default=True).first()
    texto_prompt = config.texto if config else PROMPT_REGRAS_BASE
    
    prompt_completo = f"""TEMA:
{tema}

{texto_prompt}

MUITO IMPORTANTE: Use as marcações exatas [START_NOME_DA_TAG] e [END_NOME_DA_TAG] para cada sessão.
REGRAS DE SEGURANÇA MÁXIMA (ANTI-DETECÇÃO):
1. É ESTRITAMENTE PROIBIDO fazer "meta-comentários" sobre a estrutura da prova.
2. Na Autoavaliação ou Resumo, NUNCA mencione "limites de caracteres", "exigências do prompt", "Etapa 5", ou a dificuldade de escrever o texto. Fale como se o trabalho fosse real e prático.
3. Fale APENAS sobre o caso prático, os conceitos da disciplina e o aprendizado do aluno."""
    
    fila_modelos = [modelo_selecionado] + [m for m in get_modelos_ativos() if m != modelo_selecionado]
    
    nova_task = GeracaoTask(user_id=current_user.id, status='Pendente')
    db.session.add(nova_task)
    db.session.commit()
    
    threading.Thread(target=executar_geracao_bg, args=(nova_task.id, prompt_completo, fila_modelos)).start()
    return jsonify({"sucesso": True, "task_id": nova_task.id})

@app.route('/humanizar_trabalho', methods=['POST'])
@login_required
def humanizar_trabalho():
    try:
        dados = request.json
        modelo_selecionado = dados.get('modelo', get_modelos_ativos()[0])
        contexto_atual = dados.get('dicionario', {}) 
        
        texto_contexto = "".join([f"[START_{k.replace('{{', '').replace('}}', '')}]\n{v}\n[END_{k.replace('{{', '').replace('}}', '')}]\n\n" for k, v in contexto_atual.items() if v and str(v).strip()])

        prompt_humanizador = f"""ATENÇÃO MÁXIMA: O SEU ÚNICO PAPEL AGORA É SER UM REVISOR DE ESTILO E PARAFRASEADOR ACADÊMICO DE ELITE. 
É ESTRITAMENTE PROIBIDO INVENTAR ASSUNTOS, PERSONAGENS OU CONCEITOS NOVOS.

O texto abaixo já está pronto e correto. O seu trabalho é APENAS reescrever as informações para não ser detectado por softwares de plágio de IA (Turnitin/GPTZero).

REGRAS DE BLINDAGEM (ESTILO DE ESCRITA):
1. Explosividade (Burstiness): Alterne o tamanho das frases. Escreva uma frase mais curta e direta. Depois, uma longa e explicativa.
2. Tom Acadêmico Humano: Mantenha a FORMALIDADE e o RIGOR TÉCNICO. Não seja informal. Troque o vocabulário "robótico". É PROIBIDO USAR: crucial, vital, mergulho profundo, tapeçaria, notável, locus, multifacetada, teia.
3. Anti-Meta: NUNCA mencione limites de caracteres ou termos como 'Etapa 5'.
4. Retorne usando EXATAMENTE as mesmas tags.

TEXTO ORIGINAL:
{texto_contexto}"""
        
        fila_modelos = [modelo_selecionado] + [m for m in get_modelos_ativos() if m != modelo_selecionado]
        nova_task = GeracaoTask(user_id=current_user.id, status='Pendente')
        db.session.add(nova_task)
        db.session.commit()
        
        threading.Thread(target=executar_geracao_bg, args=(nova_task.id, prompt_humanizador, fila_modelos)).start()
        return jsonify({"sucesso": True, "task_id": nova_task.id})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/cancelar_geracao/<int:task_id>', methods=['POST'])
@login_required
def cancelar_geracao(task_id):
    task = GeracaoTask.query.get(task_id)
    if task and task.user_id == current_user.id and task.status == 'Pendente':
        task.status = 'Cancelado'
        db.session.commit()
        return jsonify({"sucesso": True})
    return jsonify({"sucesso": False, "erro": "Tarefa não encontrada ou já concluída."})

@app.route('/status_geracao/<int:task_id>', methods=['GET'])
@login_required
def status_geracao(task_id):
    task = GeracaoTask.query.get(task_id)
    if not task or task.user_id != current_user.id: 
        return jsonify({"sucesso": False, "erro": "Tarefa não encontrada."})
        
    if task.status == 'Pendente': 
        return jsonify({"status": "Pendente"})
    elif task.status == 'Erro': 
        return jsonify({"status": "Erro", "erro": task.erro})
    elif task.status == 'Cancelado': 
        return jsonify({"status": "Cancelado"})
        
    return jsonify({
        "status": "Concluido", 
        "dicionario": json.loads(task.resultado), 
        "modelo_utilizado": task.modelo_utilizado
    })

@app.route('/analisar_ia_trecho', methods=['POST'])
@login_required
def analisar_ia_trecho():
    try:
        trecho = str(request.json.get('trecho', '')).strip()
        if not trecho or len(trecho) < 25: 
            return jsonify({"sucesso": True, "porcentagem": 0})
        
        prompt = f"""Você é um analista de textos acadêmicos. Avalie de 0 a 100 qual a probabilidade do texto abaixo ter sido gerado por uma Inteligência Artificial.
ATENÇÃO: Textos universitários usam naturalmente linguagem técnica, formal e culta. NÃO confunda texto técnico humano com Inteligência Artificial!

DÊ UMA NOTA ALTA (70-100%) APENAS SE: 
- Houver excesso de palavras-clichê robóticas (ex: crucial, mergulho profundo, tapeçaria, vital, locus, notável, farol, em suma, multifacetada).
- O texto não tiver variação de tamanho de frases (texto quadrado e monótono).

DÊ UMA NOTA BAIXA (0-30%) SE: 
- For direto, objetivo, técnico/médico e as frases tiverem tamanhos desiguais.

TEXTO:
{trecho}

Responda ÚNICA E EXCLUSIVAMENTE com o número da porcentagem (ex: 15). Nenhuma palavra extra."""

        resposta, custo = ia_core.chamar_ia(prompt, "google/gemini-2.5-flash", CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado="google/gemini-2.5-flash", custo=custo))
        db.session.commit()
        
        numeros = re.findall(r'\d+', resposta)
        porcentagem_final = min(int(numeros[0]), 100) if numeros else 15
        
        return jsonify({"sucesso": True, "porcentagem": porcentagem_final})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/humanizar_trecho_avulso', methods=['POST'])
@login_required
def humanizar_trecho_avulso():
    try:
        dados = request.json or {}
        modelo = dados.get('modelo', get_modelos_ativos()[0])
        
        prompt = f"""Sua missão é reescrever o texto abaixo visando 0% de detecção por softwares Anti-IA.
ATENÇÃO: Você DEVE manter o RIGOR ACADÊMICO e a FORMALIDADE TÉCNICA. Escreva como um pesquisador humano real.

TÁTICAS OBRIGATÓRIAS:
1. Explosividade: Alterne o padrão de frases. Escreva uma frase curta, depois uma mais explicativa.
2. Vocabulário: Use termos técnicos corretos, mas fuja das palavras que denunciam IA. PROIBIDO USAR: crucial, notável, vital, mergulhar, outrossim, farol, tapeçaria, locus, multifacetada.
3. PROIBIDO fazer "meta-comentários" ou dar saudações.

TEXTO ORIGINAL:
{dados.get('trecho', '')}

Retorne APENAS o novo texto limpo."""

        novo_texto, custo = ia_core.chamar_ia(prompt, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado=modelo, custo=custo))
        db.session.commit()
        
        return jsonify({"sucesso": True, "novo_texto": novo_texto.strip('* ')})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/assistente_pontual', methods=['POST'])
@login_required
def assistente_pontual():
    try:
        dados = request.json or {}
        modelo = dados.get('modelo', get_modelos_ativos()[0])
        
        prompt = f"TEXTO ORIGINAL:\n{dados.get('trecho', '')}\n\nPEDIDO:\n{dados.get('comando', '')}\n\nReescreva aplicando o pedido. Retorne APENAS o novo texto limpo de formatações excessivas."
        
        novo_texto, custo = ia_core.chamar_ia(prompt, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado=modelo, custo=custo))
        db.session.commit()
        
        return jsonify({"sucesso": True, "novo_texto": novo_texto})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/exterminar_cliches', methods=['POST'])
@login_required
def exterminar_cliches():
    try:
        prompt = f"Remova palavras clichês de IA (ex: crucial, vital, tapeçaria, locus, multifacetada, teia) deste texto, mantendo a formalidade técnica e acadêmica de forma natural:\n{request.json.get('trecho', '')}\nRetorne APENAS o texto limpo."
        
        novo_texto, custo = ia_core.chamar_ia(prompt, "google/gemini-2.5-flash", CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado="google/gemini-2.5-flash", custo=custo))
        db.session.commit()
        
        return jsonify({"sucesso": True, "novo_texto": novo_texto})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/regerar_trecho', methods=['POST'])
@login_required
def regerar_trecho():
    try:
        dados = request.json or {}
        tag = str(dados.get('tag', ''))
        modelo_selecionado = dados.get('modelo', get_modelos_ativos()[0])
        contexto_atual = dados.get('dicionario', {}) 
        
        texto_contexto = "".join([f"{k}:\n{v}\n\n" for k, v in contexto_atual.items() if v and str(v).strip()])

        prompt = f"TEMA:\n{dados.get('tema', '')}\nCONTEXTO:\n{texto_contexto}\nReescreva APENAS o trecho da tag {tag}. ATENÇÃO: NUNCA mencione limites de caracteres ou regras de formatação. Retorne APENAS o texto limpo."
        fila_modelos = [modelo_selecionado] + [m for m in get_modelos_ativos() if m != modelo_selecionado]
        
        for modelo in fila_modelos[:2]: 
            try:
                novo_texto, custo = ia_core.chamar_ia(prompt, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
                novo_texto = re.sub(rf"\[/?START_{tag}\]", "", novo_texto, flags=re.IGNORECASE)
                novo_texto = re.sub(rf"\[/?END_{tag}\]", "", novo_texto, flags=re.IGNORECASE).strip('* ')
                
                db.session.add(RegistroUso(modelo_usado=modelo, custo=custo))
                db.session.commit()
                return jsonify({"sucesso": True, "novo_texto": novo_texto, "modelo_utilizado": modelo})
            except Exception as e: 
                continue
                
        return jsonify({"sucesso": False, "erro": "Falha nas tentativas."})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/gerar_docx_final', methods=['POST'])
@login_required
def gerar_docx_final():
    try:
        dados = request.json or {}
        aluno_id = dados.get('aluno_id')
        nome_arquivo = str(dados.get('nome_arquivo', '')).strip()
        
        with open(os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx'), 'rb') as f: 
            arquivo_bytes = documentos.preencher_template_com_tags(io.BytesIO(f.read()), dados.get('dicionario', {})).read()
        
        if not nome_arquivo:
            nome_arquivo = f"Trabalho_{datetime.now().strftime('%d%m%Y')}.docx"
        elif not nome_arquivo.lower().endswith('.docx'):
            nome_arquivo += '.docx'
            
        if aluno_id:
            db.session.add(Documento(aluno_id=aluno_id, nome_arquivo=nome_arquivo, dados_arquivo=arquivo_bytes))
            aluno = Aluno.query.get(aluno_id)
            if aluno and (aluno.status == 'Produção' or not aluno.status): 
                aluno.status = 'Pendente'
            db.session.commit()
            
        return jsonify({
            "sucesso": True, 
            "nome_arquivo": nome_arquivo, 
            "arquivo_base64": base64.b64encode(arquivo_bytes).decode('utf-8')
        })
    except Exception as e: 
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/converter_pdf/<int:doc_id>', methods=['POST'])
@login_required
def converter_pdf(doc_id):
    try:
        doc = Documento.query.get_or_404(doc_id)
        if doc.aluno.user_id != current_user.id and current_user.role != 'admin': 
            return jsonify({"sucesso": False, "erro": "Acesso negado."}), 403
            
        if not doc.nome_arquivo.lower().endswith('.docx'): 
            return jsonify({"sucesso": False, "erro": "Apenas arquivos .docx podem ser convertidos."})

        config = SiteSettings.query.first()
        if not config or not config.convert_api_key: 
            return jsonify({"sucesso": False, "erro": "Cadastre a Secret Key da ConvertAPI."})

        response = requests.post(f'https://v2.convertapi.com/convert/docx/to/pdf?Secret={config.convert_api_key}', files={'File': (doc.nome_arquivo, doc.dados_arquivo)}).json()
        
        if 'Files' in response:
            novo_nome = doc.nome_arquivo.replace('.docx', '.pdf').replace('.DOCX', '.pdf')
            pdf_bytes = base64.b64decode(response['Files'][0]['FileData'])
            
            db.session.add(Documento(aluno_id=doc.aluno_id, nome_arquivo=novo_nome, dados_arquivo=pdf_bytes))
            db.session.commit()
            return jsonify({"sucesso": True})
            
        return jsonify({"sucesso": False, "erro": response.get('Message', 'Falha na conversão.')})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/banco_temas')
@login_required
def banco_temas():
    temas_brutos = db.session.query(TemaTrabalho).join(Aluno).filter(Aluno.user_id == current_user.id).order_by(TemaTrabalho.data_cadastro.desc()).all()
    temas_unicos = []
    hashes_vistos = set()
    
    for t in temas_brutos:
        texto_limpo = t.texto.strip()
        if not texto_limpo: 
            continue
            
        texto_hash = hashlib.md5(re.sub(r'[\W_]+', '', texto_limpo[:500].lower()).encode('utf-8')).hexdigest()
        
        if texto_hash not in hashes_vistos:
            hashes_vistos.add(texto_hash)
            linhas = [l.strip() for l in texto_limpo.split('\n') if l.strip()]
            
            titulo_encontrado = ""
            for linha in linhas[:5]:
                if "DESAFIO" in linha.upper():
                    titulo_encontrado = linha.upper().replace('*', '').strip()
                    break
                    
            if not titulo_encontrado and linhas and len(linhas[0]) < 100:
                titulo_encontrado = linhas[0].upper().replace('*', '').strip()
                
            t.titulo_exibicao = titulo_encontrado
            
            if not t.titulo_exibicao:
                if t.titulo and not str(t.titulo).strip().lower().startswith("tema"):
                    t.titulo_exibicao = str(t.titulo).upper()
                else:
                    nome_curso = str(t.aluno.curso).strip().upper() if t.aluno.curso else 'DISCIPLINA NÃO INFORMADA'
                    t.titulo_exibicao = f"DESAFIO PROFISSIONAL DE {nome_curso}"
                    
            temas_unicos.append(t)
            
    return render_template('banco_temas.html', temas=temas_unicos)

# =========================================================
# DASHBOARD FINANCEIRO E INDICADORES
# =========================================================
@app.route('/dashboard')
@login_required
def dashboard():
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    
    hoje_brasil = (datetime.utcnow() - timedelta(hours=3))
    
    data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date() if data_inicio_str else None
    data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date() if data_fim_str else None

    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).all()
    
    receita_hoje = 0.0
    receita_periodo = 0.0
    a_receber_periodo = 0.0
    trabalhos_periodo = 0
    
    # Lógica financeira e de clientes
    for a in todos_alunos:
        data_referencia = (a.data_pagamento or a.data_cadastro)
        data_referencia_br = (data_referencia - timedelta(hours=3)).date() if data_referencia else hoje_brasil.date()
        
        if a.status == 'Pago' and data_referencia_br == hoje_brasil.date():
            receita_hoje += (a.valor or 70.0)

        data_ref_cadastro = (a.data_cadastro - timedelta(hours=3)).date() if a.data_cadastro else hoje_brasil.date()

        in_period_pagamento = (not data_inicio or data_referencia_br >= data_inicio) and (not data_fim or data_referencia_br <= data_fim)
        in_period_cadastro = (not data_inicio or data_ref_cadastro >= data_inicio) and (not data_fim or data_ref_cadastro <= data_fim)

        if a.status == 'Pago' and in_period_pagamento: 
            receita_periodo += (a.valor or 70.0)
        if a.status != 'Pago' and in_period_cadastro: 
            a_receber_periodo += (a.valor or 70.0)
        if in_period_cadastro: 
            trabalhos_periodo += 1
            
    # Lógica de custos das IAs
    custo_periodo = 0.0
    uso_modelos_dict = {}
    for u in RegistroUso.query.all():
        d_uso_br = (u.data - timedelta(hours=3)).date()
        if (not data_inicio or d_uso_br >= data_inicio) and (not data_fim or d_uso_br <= data_fim):
            custo_periodo += (u.custo or 0.0)
            uso_modelos_dict.setdefault(u.modelo_usado, {'count': 0, 'custo': 0.0})
            uso_modelos_dict[u.modelo_usado]['count'] += 1
            uso_modelos_dict[u.modelo_usado]['custo'] += (u.custo or 0.0)

    uso_modelos_lista = [(k, v['count'], v['custo']) for k, v in uso_modelos_dict.items()]
    
    # Cálculos dos gráficos
    labels_meses = [(hoje_brasil.year - (1 if hoje_brasil.month - i <= 0 else 0), hoje_brasil.month - i + (12 if hoje_brasil.month - i <= 0 else 0)) for i in range(5, -1, -1)]
    faturamento_dict = {k: 0.0 for k in labels_meses}
    pedidos_dias = [0] * 7
    
    for a in todos_alunos:
        if a.data_cadastro:
            dia_semana = (a.data_cadastro - timedelta(hours=3)).weekday()
            pedidos_dias[dia_semana] += 1
            
            if a.status == 'Pago' and (a.data_pagamento or a.data_cadastro):
                db_date = (a.data_pagamento or a.data_cadastro) - timedelta(hours=3)
                chave_mes = (db_date.year, db_date.month)
                if chave_mes in faturamento_dict: 
                    faturamento_dict[chave_mes] += (a.valor or 70.0)
                    
    meses_nomes = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    graf_meses_lbl = [f"{meses_nomes[m-1]}/{str(y)[2:]}" for y, m in labels_meses]
    graf_meses_val = [faturamento_dict[k] for k in labels_meses]
    
    saldo_openrouter = ia_core.consultar_saldo_openrouter(CHAVE_OPENROUTER)
    
    return render_template(
        'dashboard.html', 
        receita_hoje=receita_hoje, 
        receita_periodo=receita_periodo, 
        a_receber_periodo=a_receber_periodo, 
        custo_periodo=custo_periodo, 
        saldo_real_openrouter=saldo_openrouter, 
        trabalhos_periodo=trabalhos_periodo, 
        uso_modelos=uso_modelos_lista, 
        graf_meses_lbl=graf_meses_lbl, 
        graf_meses_val=graf_meses_val, 
        graf_dias_lbl=['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'], 
        graf_dias_val=pedidos_dias,
        data_inicio=data_inicio_str or '', 
        data_fim=data_fim_str or '', 
        filtrado=bool(data_inicio_str or data_fim_str)
    )

# =========================================================
# CRM: CLIENTES E STATUS
# =========================================================
@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    if request.method == 'POST':
        try: 
            valor_float = float(request.form.get('valor', '70.0').replace(',', '.'))
        except ValueError: 
            valor_float = 70.0
            
        novo_aluno = Aluno(
            user_id=current_user.id, 
            nome=request.form.get('nome'), 
            curso=request.form.get('curso'), 
            telefone=request.form.get('telefone'), 
            ava_login=request.form.get('ava_login'), 
            ava_senha=request.form.get('ava_senha'), 
            valor=valor_float
        )
        db.session.add(novo_aluno)
        db.session.commit()
        flash('Cliente cadastrado com sucesso!', 'success')
        return redirect(url_for('clientes'))
        
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.id.desc()).all()
    hoje_brasil = (datetime.utcnow() - timedelta(hours=3)).date()
    pagos_agrupados = {}
    
    for a in [al for al in todos_alunos if al.status == 'Pago']:
        data_ref = a.data_pagamento or a.data_cadastro
        data_br = (data_ref - timedelta(hours=3)).date() if data_ref else hoje_brasil
        
        texto_dia = '(Hoje)' if data_br == hoje_brasil else '(Ontem)' if data_br == hoje_brasil - timedelta(days=1) else ''
        label = f"{data_br.strftime('%d/%m/%Y')} {texto_dia}".strip()
        
        if data_br not in pagos_agrupados:
            pagos_agrupados[data_br] = {'label': label, 'alunos': []}
        pagos_agrupados[data_br]['alunos'].append(a)

    alunos_pendentes = [a for a in todos_alunos if a.status != 'Pago']
    grupos_ordenados = [pagos_agrupados[k] for k in sorted(pagos_agrupados.keys(), reverse=True)]

    return render_template(
        'clientes.html', 
        alunos_pendentes=alunos_pendentes, 
        pagos_agrupados=grupos_ordenados, 
        config=SiteSettings.query.first()
    )

@app.route('/exportar_contatos')
@login_required
def exportar_contatos():
    si = io.StringIO()
    cw = csv.writer(si, delimiter=';') 
    cw.writerow(['Nome do Aluno', 'WhatsApp', 'Curso / Disciplina', 'Status do Trabalho', 'Valor Cobrado (R$)', 'Data de Entrada'])
    
    for a in Aluno.query.filter_by(user_id=current_user.id).all():
        valor_str = f"{a.valor:.2f}".replace('.', ',') if a.valor else '0,00'
        data_str = a.data_cadastro.strftime('%d/%m/%Y') if a.data_cadastro else 'Não informada'
        cw.writerow([a.nome, a.telefone, a.curso, a.status, valor_str, data_str])
        
    return Response(
        '\ufeff' + si.getvalue(), 
        mimetype="text/csv", 
        headers={"Content-Disposition": "attachment;filename=Contatos_HubMaster.csv"}
    )

@app.route('/mudar_status/<int:id>', methods=['POST'])
@login_required
def mudar_status(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    aluno.status = request.form.get('novo_status')
    
    if aluno.status == 'Pago' and not aluno.data_pagamento: 
        aluno.data_pagamento = datetime.utcnow()
        
    db.session.commit()
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
    aluno.ava_login = request.form.get('ava_login')
    aluno.ava_senha = request.form.get('ava_senha')
    
    try: 
        aluno.valor = float(request.form.get('valor', '70.0').replace(',', '.'))
    except ValueError: 
        pass
        
    db.session.commit()
    flash('Dados do cliente atualizados!', 'success')
    return redirect(url_for('clientes'))

@app.route('/deletar_cliente/<int:id>', methods=['GET'])
@login_required
def deletar_cliente(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    db.session.delete(aluno)
    db.session.commit()
    flash('Cliente apagado.', 'success')
    return redirect(url_for('clientes'))

@app.route('/cliente/<int:id>', methods=['GET'])
@login_required
def cliente_detalhe(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
    return render_template('cliente_detalhe.html', aluno=aluno)

# =========================================================
# CRM: TEMAS E DOCUMENTOS
# =========================================================
@app.route('/adicionar_tema/<int:aluno_id>', methods=['POST'])
@login_required
def adicionar_tema(aluno_id):
    aluno = Aluno.query.get_or_404(aluno_id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    texto = request.form.get('texto')
    if texto: 
        titulo = request.form.get('titulo') or f"Tema {len(aluno.temas)+1}"
        db.session.add(TemaTrabalho(aluno_id=aluno.id, titulo=titulo, texto=texto))
        db.session.commit()
        flash('Tema salvo com sucesso!', 'success')
        
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/editar_tema/<int:tema_id>', methods=['POST'])
@login_required
def editar_tema(tema_id):
    tema = TemaTrabalho.query.get_or_404(tema_id)
    aluno = Aluno.query.get(tema.aluno_id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': 
        abort(403)
        
    tema.titulo = request.form.get('titulo')
    tema.texto = request.form.get('texto')
    db.session.commit()
    flash('Tema atualizado!', 'success')
    return redirect(url_for('cliente_detalhe', id=tema.aluno_id))

@app.route('/deletar_tema/<int:tema_id>')
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
    try:
        arquivo = request.files.get('arquivo')
        if arquivo and arquivo.filename.lower().endswith(('.docx', '.pdf')):
            db.session.add(Documento(aluno_id=aluno_id, nome_arquivo=arquivo.filename, dados_arquivo=arquivo.read()))
            db.session.commit()
            flash('Documento anexado!', 'success')
        else:
            flash('Apenas arquivos .docx e .pdf são permitidos.', 'error')
    except Exception as e: 
        flash(f'Erro ao fazer upload: {e}', 'error')
        
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/download_doc/<int:doc_id>')
def download_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    return send_file(
        io.BytesIO(doc.dados_arquivo), 
        download_name=doc.nome_arquivo, 
        as_attachment=True
    )

@app.route('/rename_doc/<int:doc_id>', methods=['POST'])
@login_required
def rename_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    novo_nome = request.form.get('novo_nome', '').strip()
    
    if novo_nome:
        extensao_original = os.path.splitext(doc.nome_arquivo)[1].lower()
        if not novo_nome.lower().endswith(extensao_original):
            novo_nome += extensao_original
        doc.nome_arquivo = novo_nome
        db.session.commit()
        flash('Arquivo renomeado com sucesso!', 'success')
        
    return redirect(url_for('cliente_detalhe', id=doc.aluno_id))

@app.route('/delete_doc/<int:doc_id>')
@login_required
def delete_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    aluno_id = doc.aluno_id
    db.session.delete(doc)
    db.session.commit()
    flash('Documento apagado.', 'success')
    return redirect(url_for('cliente_detalhe', id=aluno_id))

# =========================================================
# CONFIGURAÇÕES GERAIS, PROMPTS E ADMINISTRAÇÃO
# =========================================================
@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    config = SiteSettings.query.first()
    
    if request.method == 'POST':
        config.whatsapp_template = request.form.get('whatsapp_template')
        config.prompt_password = request.form.get('prompt_password')
        config.convert_api_key = request.form.get('convert_api_key')
        
        modelos = request.form.getlist('modelos_ativos')
        novo_modelo = request.form.get('novo_modelo')
        if novo_modelo and novo_modelo.strip() not in modelos: 
            modelos.append(novo_modelo.strip())
            
        config.modelos_ativos = ",".join(modelos)
        db.session.commit()
        flash('Configurações salvas com sucesso!', 'success')
        return redirect(url_for('configuracoes'))
        
    ativos_atuais = get_modelos_ativos()
    todos_para_exibir = list(TODOS_MODELOS_CONHECIDOS) + [m for m in ativos_atuais if m not in TODOS_MODELOS_CONHECIDOS]
    
    return render_template(
        'configuracoes.html', 
        config=config, 
        todos_modelos=todos_para_exibir, 
        modelos_ativos=ativos_atuais
    )

@app.route('/prompts')
@login_required
def prompts():
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
    return render_template('prompts.html', prompts=PromptConfig.query.all())

@app.route('/prompts/action', methods=['POST'])
@login_required
def prompts_action():
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    config = SiteSettings.query.first()
    if config and config.prompt_password:
        if request.form.get('senha_master') != config.prompt_password:
            flash('Senha Master incorreta!', 'error')
            return redirect(url_for('prompts'))

    acao = request.form.get('acao')
    prompt_id = request.form.get('prompt_id')
    
    if acao == 'add': 
        db.session.add(PromptConfig(nome=request.form.get('nome'), texto=request.form.get('texto')))
        flash('Prompt criado com sucesso!', 'success')
    elif acao == 'edit' and prompt_id: 
        p = PromptConfig.query.get(prompt_id)
        p.nome = request.form.get('nome')
        p.texto = request.form.get('texto')
        flash('Prompt atualizado com sucesso!', 'success')
    elif acao == 'delete' and prompt_id:
        p = PromptConfig.query.get(prompt_id)
        if p and p.is_default: 
            flash('Você não pode apagar o padrão base do sistema!', 'error')
        elif p: 
            db.session.delete(p)
            flash('Prompt apagado com sucesso!', 'success')

    db.session.commit()
    return redirect(url_for('prompts'))

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    if request.method == 'POST':
        data_exp = request.form.get('expiration_date')
        data_formatada = datetime.strptime(data_exp, '%Y-%m-%d').date() if data_exp else None
        
        novo_user = User(
            username=request.form.get('username'), 
            password=generate_password_hash(request.form.get('password')), 
            role=request.form.get('role'), 
            creditos=0, 
            expiration_date=data_formatada
        )
        db.session.add(novo_user)
        db.session.commit()
        flash('Usuário criado com sucesso.', 'success')
        
    return render_template('admin.html', users=User.query.all(), hoje=date.today())

@app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_user(id):
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    user = User.query.get_or_404(id)
    if request.method == 'POST':
        if request.form.get('password'): 
            user.password = generate_password_hash(request.form.get('password'))
            
        if current_user.role == 'admin': 
            user.role = request.form.get('role')
            try: 
                user.creditos = int(request.form.get('creditos')) if request.form.get('creditos') is not None else user.creditos
            except ValueError: 
                pass
                
        data_exp = request.form.get('expiration_date')
        user.expiration_date = datetime.strptime(data_exp, '%Y-%m-%d').date() if data_exp else None
        
        db.session.commit()
        flash('Usuário atualizado com sucesso!', 'success')
        return redirect(url_for('admin'))
        
    return render_template('edit_user.html', user=user)

@app.route('/delete_user/<int:id>')
@login_required
def delete_user(id):
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
        
    user_to_delete = User.query.get_or_404(id)
    db.session.delete(user_to_delete)
    db.session.commit()
    flash('Usuário deletado.', 'success')
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True)
